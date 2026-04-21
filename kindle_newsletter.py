import os
import datetime
import smtplib
import email
import requests
import hashlib
import re
import io
import time
from email.message import EmailMessage
from imapclient import IMAPClient
from readability import Document
from ebooklib import epub
from lxml import html
from PIL import Image, ImageDraw, ImageFont

try:
    from lxml.html.clean import Cleaner
except ImportError:
    # Fallback for older lxml or missing lxml_html_clean
    Cleaner = None

# --- Configuration ---
GMAIL_USER = os.getenv('GMAIL_USER')
GMAIL_APP_PASS = os.getenv('GMAIL_APP_PASSWORD')
KINDLE_EMAIL = os.getenv('KINDLE_EMAIL')
SOURCE_LABEL = 'Daily-Digest'
PROCESSED_LABEL = 'Daily-Digest/Processed'

DEFAULT_STYLE = '''
@page { margin: 5pt; }
body { font-family: "Malgun Gothic", "Apple SD Gothic Neo", "Nanum Gothic", sans-serif; line-height: 1.5; margin: 10px; }
h1 { text-align: center; font-size: 1.4em; margin-bottom: 0.5em; }
h2 { font-size: 1.2em; border-bottom: 1px solid #ccc; padding-bottom: 5px; margin-top: 20px; }
h3 { font-size: 1.1em; margin-top: 15px; }
p { margin-bottom: 1em; text-align: justify; }
img { 
    max-width: 100%; 
    height: auto; 
    display: block; 
    margin: 15px auto; 
}
table { width: 100%; border-collapse: collapse; margin: 10px 0; }
td { padding: 5px; border-bottom: 1px solid #eee; }
'''

IMAGE_ID_COUNTER = 0

def strip_emojis(text):
    """Remove characters outside the Basic Multilingual Plane (emojis)."""
    if not text: return ""
    return re.sub(r'[^\u0000-\uFFFF]', '', text)

def fetch_with_retry(url, retries=3, timeout=10):
    """Fetch URL with retries and basic error handling."""
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'}
    for i in range(retries):
        try:
            res = requests.get(url, timeout=timeout, headers=headers)
            res.raise_for_status()
            return res.content, res.headers.get('Content-Type', 'image/jpeg')
        except Exception as e:
            if i == retries - 1:
                print(f"Final attempt failed for {url}: {e}")
            else:
                time.sleep(1)
    return None, None

def advanced_cleanup(tree):
    """Remove common newsletter noise like social icons and unsubscribe links."""
    noise_patterns = [
        'unsubscribe', 'view in browser', 'manage preferences', 'privacy policy',
        'update profile', 'email preferences', 'terms of service', 'copyright',
        'all rights reserved', 'click here to view'
    ]
    
    noise_domains = [
        'facebook.com', 'twitter.com', 'linkedin.com', 'instagram.com', 
        'youtube.com', 'pinterest.com', 'plus.google.com', 'tiktok.com'
    ]

    # Remove links containing noise keywords or domains
    for link in tree.xpath('//a'):
        href = link.get('href', '').lower()
        text = link.text_content().lower()
        
        if any(p in text for p in noise_patterns) or any(d in href for d in noise_domains):
            parent = link.getparent()
            if parent is not None:
                link.getparent().remove(link)
                # If parent is now empty (or only whitespace), try to remove it too
                if not parent.text_content().strip() and not parent.xpath('.//img'):
                    try:
                        parent.getparent().remove(parent)
                    except:
                        pass

    # Remove common social media icons
    for img in tree.xpath('//img'):
        alt = (img.get('alt') or '').lower()
        src = (img.get('src') or '').lower()
        if any(social in alt for social in ['facebook', 'twitter', 'linkedin', 'instagram', 'youtube', 'rss']) or \
           any(social in src for social in ['facebook', 'twitter', 'linkedin', 'instagram', 'youtube']):
            try:
                img.getparent().remove(img)
            except:
                pass

    return tree

def clean_html_safe(raw_html):
    """Fallback cleaner for newsletters that Readability fails on."""
    if Cleaner is None:
        return raw_html
        
    cleaner = Cleaner(
        scripts=True,
        javascript=True,
        comments=True,
        style=True,
        links=False,
        meta=True,
        page_structure=False,
        processing_instructions=True,
        embedded=True,
        frames=True,
        forms=True,
        annoying_tags=True,
        remove_tags=['span', 'font', 'div']
    )
    
    try:
        parser = html.HTMLParser(encoding='utf-8')
        tree = html.fromstring(raw_html.encode('utf-8'), parser=parser)
        
        # Remove spacer rows/cells
        for node in tree.xpath('//td[@height] | //tr[@height]'):
            h = node.get('height')
            if h and h.isdigit() and int(h) < 30:
                if not node.text_content().strip():
                    node.getparent().remove(node)

        # Strip layout attributes
        for tag in tree.xpath('//table | //td | //tr | //th | //img'):
            for attr in ['width', 'height', 'style', 'bgcolor', 'background', 'valign', 'align']:
                if attr in tag.attrib:
                    if tag.tag == 'img' and attr in ['width', 'height']:
                        continue
                    del tag.attrib[attr]

        tree = advanced_cleanup(tree)
        cleaned_node = cleaner.clean_html(tree)
        return html.tostring(cleaned_node, encoding='unicode', method='html')
    except Exception as e:
        print(f"Safe clean failed: {e}")
        return raw_html

def get_email_data(msg_bytes):
    msg = email.message_from_bytes(msg_bytes)
    subject = msg.get('Subject', 'No Subject')
    from_header = msg.get('From', 'Unknown Sender')
    
    subject_parts = email.header.decode_header(subject)
    decoded_subject = ""
    for part, encoding in subject_parts:
        if isinstance(part, bytes):
            decoded_subject += part.decode(encoding or 'utf-8', errors='ignore')
        else:
            decoded_subject += part
    
    html_content = ""
    cid_images = {}

    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            disposition = str(part.get("Content-Disposition"))
            if content_type == "text/html" and "attachment" not in disposition:
                payload = part.get_payload(decode=True)
                charset = part.get_content_charset() or 'utf-8'
                html_content = payload.decode(charset, errors='ignore')
            elif content_type.startswith("image/"):
                cid = part.get("Content-ID")
                if cid:
                    cid = cid.strip('<>')
                    cid_images[cid] = {
                        'data': part.get_payload(decode=True),
                        'type': content_type
                    }
    else:
        payload = msg.get_payload(decode=True)
        charset = msg.get_content_charset() or 'utf-8'
        html_content = payload.decode(charset, errors='ignore')

    return decoded_subject, from_header, html_content, cid_images

def process_images(book, html_str, cid_images):
    global IMAGE_ID_COUNTER
    if not html_str: return ""
    
    try:
        parser = html.HTMLParser(encoding='utf-8')
        tree = html.fromstring(html_str.encode('utf-8'), parser=parser)
    except Exception:
        return html_str

    for img in tree.xpath('//img'):
        src = img.get('src')
        data_src = img.get('data-src') or img.get('data-original-src') or img.get('original-src')
        target_url = data_src or src
        if not target_url: continue
        
        if 'googleusercontent.com/meips/' in target_url and '#' in target_url:
            target_url = target_url.split('#')[-1]
            
        img_data = None
        img_type = "image/jpeg"
        
        if target_url.startswith('cid:'):
            cid = target_url[4:]
            if cid in cid_images:
                img_data = cid_images[cid]['data']
                img_type = cid_images[cid]['type']
        elif target_url.startswith('http'):
            if any(x in target_url.lower() for x in ['pixel', 'click.pstmrk.it', 'open.track', 'spacer', 'tracking']):
                img.getparent().remove(img)
                continue
            
            w = img.get('width')
            h = img.get('height')
            if w == '1' or h == '1':
                img.getparent().remove(img)
                continue
                
            img_data, img_type = fetch_with_retry(target_url)

        if img_data:
            try:
                # Use PIL to validate and potentially convert image
                pil_img = Image.open(io.BytesIO(img_data))
                
                # Convert to RGB if necessary (Kindle likes RGB JPEG/PNG)
                if pil_img.mode in ("RGBA", "P"):
                    pil_img = pil_img.convert("RGB")
                
                # Resize if too large
                max_width = 800
                if pil_img.width > max_width:
                    ratio = max_width / float(pil_img.width)
                    new_height = int(float(pil_img.height) * ratio)
                    pil_img = pil_img.resize((max_width, new_height), Image.Resampling.LANCZOS)
                
                # Save as JPEG for best compatibility
                output = io.BytesIO()
                pil_img.save(output, format='JPEG', quality=85)
                img_data = output.getvalue()
                m_type, ext = 'image/jpeg', 'jpg'
                
                img_filename = f"images/img_{IMAGE_ID_COUNTER}.{ext}"
                epub_img = epub.EpubItem(
                    uid=f"img_{IMAGE_ID_COUNTER}",
                    file_name=img_filename,
                    media_type=m_type,
                    content=img_data
                )
                book.add_item(epub_img)
                img.set('src', f"../{img_filename}")
                IMAGE_ID_COUNTER += 1
                print(f"Added image: {img_filename} ({pil_img.width}x{pil_img.height})")
            except Exception as e:
                print(f"Image processing failed for {target_url}: {e}")
                img.getparent().remove(img)
        else:
            img.getparent().remove(img)
            
    return html.tostring(tree, encoding='unicode', method='xml')

def process_newsletters():
    articles = []
    with IMAPClient('imap.gmail.com') as client:
        client.login(GMAIL_USER, GMAIL_APP_PASS)
        if not client.folder_exists(PROCESSED_LABEL): client.create_folder(PROCESSED_LABEL)
        client.select_folder(SOURCE_LABEL)
        messages = client.search(['NOT', 'DELETED'])
        
        if not messages: return None

        fetch_data = client.fetch(messages, 'RFC822')
        for msgid in messages:
            if msgid not in fetch_data: continue
            
            subject, sender, raw_html, cid_images = get_email_data(fetch_data[msgid][b'RFC822'])
            
            doc = Document(raw_html)
            title = doc.short_title()
            if not title or any(x in title.lower() for x in ['no title', 'untitled', 'no-title']):
                title = subject
            
            content = doc.summary()
            plain_text = re.sub('<[^<]+?>', '', content).strip()
            if (len(content) < 600 and len(raw_html) > 3000) or len(plain_text) < 100:
                print(f"Readability failed for '{title}', using safe cleaner fallback.")
                content = clean_html_safe(raw_html)
            else:
                # Even if Readability works, run advanced cleanup on the result
                parser = html.HTMLParser(encoding='utf-8')
                tree = html.fromstring(content.encode('utf-8'), parser=parser)
                tree = advanced_cleanup(tree)
                content = html.tostring(tree, encoding='unicode', method='html')
            
            content = strip_emojis(content)
            title = strip_emojis(title)
            
            articles.append({
                'title': title,
                'content': content,
                'cid_images': cid_images,
                'sender': sender
            })
            client.copy(msgid, PROCESSED_LABEL)
            client.delete_messages(msgid)
        client.expunge()
    return articles

def generate_cover_image(title, date_str):
    """Generate a simple, modern cover image for the EPUB."""
    width, height = 600, 800
    # Dark blue-grey background
    image = Image.new('RGB', (width, height), color=(44, 62, 80))
    draw = ImageDraw.Draw(image)
    
    # Try to find a standard font
    font_paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", # Ubuntu
        "/System/Library/Fonts/Helvetica.ttc",                # macOS
        "/Library/Fonts/Arial.ttf",                           # macOS
        "arial.ttf"                                           # Windows
    ]
    
    title_font = None
    date_font = None
    for path in font_paths:
        try:
            if os.path.exists(path) or path == "arial.ttf":
                title_font = ImageFont.truetype(path, 45)
                date_font = ImageFont.truetype(path, 30)
                break
        except:
            continue
            
    if not title_font:
        title_font = ImageFont.load_default()
        date_font = ImageFont.load_default()

    # Draw Title
    title_w = draw.textlength(title, font=title_font) if hasattr(draw, 'textlength') else 100
    draw.text(((width - title_w)/2, height/3), title, fill=(236, 240, 241), font=title_font)
    
    # Draw Date
    date_w = draw.textlength(date_str, font=date_font) if hasattr(draw, 'textlength') else 100
    draw.text(((width - date_w)/2, height/2), date_str, fill=(189, 195, 199), font=date_font)
    
    # Draw a simple accent line
    draw.rectangle([width/4, height/2.5, 3*width/4, height/2.5 + 5], fill=(52, 152, 219))

    img_byte_arr = io.BytesIO()
    image.save(img_byte_arr, format='JPEG', quality=90)
    return img_byte_arr.getvalue()

def create_epub(articles):
    date_str = datetime.date.today().strftime("%B %d, %Y")
    filename = f"Daily_Digest_{datetime.date.today().isoformat()}.epub"
    
    book = epub.EpubBook()
    book.set_identifier(f'digest-{datetime.date.today().isoformat()}')
    book.set_title(f'Daily Digest - {date_str}')
    
    has_korean = any(re.search('[\u3131-\u3163\uac00-\ud7a3]+', art['content']) for art in articles)
    book.set_language('ko' if has_korean else 'en')
    
    # Set Cover
    if os.path.exists('cover.jpg'):
        with open('cover.jpg', 'rb') as f:
            book.set_cover("cover.jpg", f.read())
    else:
        cover_content = generate_cover_image("Daily Digest", date_str)
        book.set_cover("cover.jpg", cover_content)
    
    style_item = epub.EpubItem(uid="style_default", file_name="style/default.css", media_type="text/css", content=DEFAULT_STYLE)
    book.add_item(style_item)

    chapters = []
    for i, art in enumerate(articles):
        # Process images and ensure valid internal structure
        processed_content = process_images(book, art['content'], art['cid_images'])
        
        # Strip potential full html tags from processed_content to avoid nesting
        if '<body' in processed_content:
            try:
                tree = html.fromstring(processed_content)
                body = tree.find('.//body')
                if body is not None:
                    processed_content = ''.join([html.tostring(child, encoding='unicode') for child in body])
            except:
                pass

        chapter = epub.EpubHtml(title=art['title'], file_name=f'text/chap_{i}.xhtml', lang='ko' if has_korean else 'en')
        chapter.content = f"<h1>{art['title']}</h1><p style='text-align:center'><small>From: {art['sender']}</small></p><div>{processed_content}</div>"
        chapter.add_item(style_item)
        
        book.add_item(chapter)
        chapters.append(chapter)

    book.toc = tuple(chapters)
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.spine = ['nav'] + chapters
    
    epub.write_epub(filename, book)
    return filename

def send_emails(filepath):
    recipients = [KINDLE_EMAIL, GMAIL_USER]
    for recipient in recipients:
        msg = EmailMessage()
        msg['Subject'] = f'Daily Digest - {datetime.date.today().isoformat()}'
        msg['From'] = GMAIL_USER
        msg['To'] = recipient
        msg.set_content("Your Daily Digest is ready.")
        with open(filepath, 'rb') as f:
            msg.add_attachment(f.read(), maintype='application', subtype='epub+zip', filename=filepath)
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp:
            smtp.login(GMAIL_USER, GMAIL_APP_PASS)
            smtp.send_message(msg)
        print(f"Sent to {recipient}")

if __name__ == "__main__":
    if not all([GMAIL_USER, GMAIL_APP_PASS, KINDLE_EMAIL]):
        print("Missing environment variables.")
        exit(1)
    arts = process_newsletters()
    if arts:
        f = create_epub(arts)
        send_emails(f)
    else:
        print("Nothing to process.")
