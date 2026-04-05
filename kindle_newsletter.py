import os
import datetime
import smtplib
import email
import requests
import hashlib
from email.message import EmailMessage
from imapclient import IMAPClient
from readability import Document
from ebooklib import epub
from lxml import html

# --- Configuration ---
GMAIL_USER = os.getenv('GMAIL_USER')
GMAIL_APP_PASS = os.getenv('GMAIL_APP_PASSWORD')
KINDLE_EMAIL = os.getenv('KINDLE_EMAIL')
SOURCE_LABEL = 'Daily-Digest'
PROCESSED_LABEL = 'Daily-Digest/Processed'

# CSS to ensure images and emojis don't overflow and emojis stay small
DEFAULT_STYLE = '''
@page { margin: 5pt; }
body { font-family: sans-serif; line-height: 1.5; }
h1 { text-align: center; font-size: 1.4em; margin-bottom: 0.5em; }
p { margin-bottom: 1em; }

/* Large images: Responsive but not overwhelming */
img { 
    max-width: 100%; 
    height: auto; 
    display: block; 
    margin: 15px auto; 
}

/* Small icons/emojis: Keep them inline and same height as text */
img[width="1"], img[height="1"] { display: none; }

/* Target images that are likely emojis (usually small in newsletters) */
img.emoji, 
img[width*="px"][width^="1"], img[width*="px"][width^="2"], img[width*="px"][width^="3"],
img[style*="width: 1"], img[style*="width: 2"], img[style*="width: 3"] {
    display: inline-block !important;
    height: 1.2em !important;
    width: auto !important;
    vertical-align: middle;
    margin: 0 0.1em;
}
'''

# Global counter to ensure unique image IDs across all chapters
IMAGE_ID_COUNTER = 0

def get_email_data(msg_bytes):
    """Extracts subject, HTML body, and CID attachments from email bytes."""
    msg = email.message_from_bytes(msg_bytes)
    subject = msg.get('Subject', 'No Subject')
    from_header = msg.get('From', 'Unknown Sender')
    
    # Decode subject
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
                html_content = part.get_payload(decode=True).decode(part.get_content_charset() or 'utf-8', errors='ignore')
            elif content_type.startswith("image/"):
                cid = part.get("Content-ID")
                if cid:
                    cid = cid.strip('<>')
                    cid_images[cid] = {
                        'data': part.get_payload(decode=True),
                        'type': content_type
                    }
    else:
        html_content = msg.get_payload(decode=True).decode(msg.get_content_charset() or 'utf-8', errors='ignore')

    return decoded_subject, from_header, html_content, cid_images

def process_images(book, html_str, cid_images):
    """Downloads remote images and maps CID images into the EPUB book."""
    global IMAGE_ID_COUNTER
    try:
        tree = html.fromstring(html_str)
    except Exception:
        return html_str # Return original if parsing fails

    for img in tree.xpath('//img'):
        src = img.get('src')
        if not src:
            continue
            
        img_data = None
        img_type = "image/jpeg"
        
        # Handle CID (embedded) images
        if src.startswith('cid:'):
            cid = src[4:]
            if cid in cid_images:
                img_data = cid_images[cid]['data']
                img_type = cid_images[cid]['type']
        
        # Handle remote images
        elif src.startswith('http'):
            try:
                res = requests.get(src, timeout=10, headers={'User-Agent': 'Mozilla/5.0'})
                if res.status_code == 200:
                    img_data = res.content
                    img_type = res.headers.get('Content-Type', 'image/jpeg')
            except Exception as e:
                print(f"Failed to download image {src}: {e}")

        if img_data:
            ext = img_type.split('/')[-1].split(';')[0] or 'jpg'
            # Sanitize extension
            if 'png' in ext: ext = 'png'
            elif 'gif' in ext: ext = 'gif'
            else: ext = 'jpg'
            
            img_filename = f"images/img_{IMAGE_ID_COUNTER}.{ext}"
            
            epub_img = epub.EpubItem(
                uid=f"img_{IMAGE_ID_COUNTER}",
                file_name=img_filename,
                media_type=img_type,
                content=img_data
            )
            book.add_item(epub_img)
            
            # Update HTML to point to the local file
            img.set('src', img_filename)
            
            # If the image is very small (potential emoji), give it a class
            w = img.get('width', '')
            if w and w.isdigit() and int(w) < 50:
                img.set('class', 'emoji')
            
            IMAGE_ID_COUNTER += 1
            
    return html.tostring(tree, encoding='unicode')

def process_newsletters():
    articles = []
    with IMAPClient('imap.gmail.com') as client:
        client.login(GMAIL_USER, GMAIL_APP_PASS)
        
        if not client.folder_exists(PROCESSED_LABEL):
            client.create_folder(PROCESSED_LABEL)
            
        client.select_folder(SOURCE_LABEL)
        messages = client.search(['NOT', 'DELETED'])
        
        if not messages:
            print("No new newsletters found.")
            return None

        for msgid, data in client.fetch(messages, 'RFC822').items():
            subject, sender, raw_html, cid_images = get_email_data(data[b'RFC822'])
            
            doc = Document(raw_html)
            title = doc.short_title()
            if not title or title.lower() in ['[no-title]', 'untitled', 'no title', 'no-title']:
                title = subject
            
            articles.append({
                'title': title,
                'content': doc.summary(),
                'cid_images': cid_images,
                'sender': sender
            })
            
            client.copy(msgid, PROCESSED_LABEL)
            client.delete_messages(msgid)
        
        client.expunge()
    return articles

def create_epub(articles):
    date_str = datetime.date.today().strftime("%B %d, %Y")
    filename = f"Daily_Digest_{datetime.date.today().isoformat()}.epub"
    
    book = epub.EpubBook()
    book.set_identifier(f'digest-{datetime.date.today().isoformat()}')
    book.set_title(f'Daily Digest - {date_str}')
    book.set_language('en')
    
    # Add CSS
    style_item = epub.EpubItem(uid="style_default", file_name="style/default.css", media_type="text/css", content=DEFAULT_STYLE)
    book.add_item(style_item)

    chapters = []
    for i, art in enumerate(articles):
        processed_content = process_images(book, art['content'], art['cid_images'])
        
        chapter = epub.EpubHtml(title=art['title'], file_name=f'chap_{i}.xhtml', lang='en')
        chapter.content = f"<h1>{art['title']}</h1><p><small>From: {art['sender']}</small></p>{processed_content}"
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
        msg.set_content("Your curated newsletters are attached.")

        with open(filepath, 'rb') as f:
            msg.add_attachment(f.read(), maintype='application', subtype='epub+zip', filename=filepath)

        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp:
            smtp.login(GMAIL_USER, GMAIL_APP_PASS)
            smtp.send_message(msg)
        print(f"Sent to {recipient}")

if __name__ == "__main__":
    if not all([GMAIL_USER, GMAIL_APP_PASS, KINDLE_EMAIL]):
        print("Error: Missing env variables.")
        exit(1)
        
    articles = process_newsletters()
    if articles:
        epub_file = create_epub(articles)
        send_emails(epub_file)
    else:
        print("Nothing to process.")
