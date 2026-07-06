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
from google import genai

try:
    from lxml.html.clean import Cleaner
except ImportError:
    # Fallback for older lxml or missing lxml_html_clean
    Cleaner = None

# --- Configuration ---
GMAIL_USER = os.getenv('GMAIL_USER')
GMAIL_APP_PASS = os.getenv('GMAIL_APP_PASSWORD')
KINDLE_EMAIL = os.getenv('KINDLE_EMAIL')
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')
SOURCE_LABEL = 'Daily-Digest'
PROCESSED_LABEL = 'Daily-Digest/Processed'
DEBUG = os.getenv('DEBUG', 'false').lower() == 'true'
USER_LOCATION = os.getenv('USER_LOCATION')

def fetch_weather(location):
    """Fetch current weather for a city using Open-Meteo API."""
    if not location:
        return None
    try:
        print(f"Fetching weather coordinates for {location}...")
        geo_url = f"https://geocoding-api.open-meteo.com/v1/search?name={requests.utils.quote(location)}&count=1&language=en&format=json"
        geo_res = requests.get(geo_url, timeout=10)
        geo_res.raise_for_status()
        geo_data = geo_res.json()
        if not geo_data.get('results'):
            print(f"No geocoding results found for {location}")
            return None
        
        result = geo_data['results'][0]
        lat = result['latitude']
        lon = result['longitude']
        resolved_name = result.get('name', location)
        country = result.get('country_code', '')
        location_str = f"{resolved_name}, {country.upper()}" if country else resolved_name
        
        print(f"Resolved coordinates: {lat}, {lon}. Fetching weather...")
        is_us = country.upper() == 'US'
        temp_unit = "fahrenheit" if is_us else "celsius"
        weather_url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current=temperature_2m,apparent_temperature,weather_code&temperature_unit={temp_unit}"
        
        weather_res = requests.get(weather_url, timeout=10)
        weather_res.raise_for_status()
        weather_data = weather_res.json()
        current = weather_data.get('current', {})
        temp = round(current.get('temperature_2m', 0))
        code = current.get('weather_code', 0)
        
        weather_icons = {
            0: ("☀️", "Clear"),
            1: ("🌤️", "Mainly Clear"),
            2: ("⛅", "Partly Cloudy"),
            3: ("☁️", "Overcast"),
            45: ("🌫️", "Fog"),
            48: ("🌫️", "Depositing Rime Fog"),
            51: ("🌧️", "Light Drizzle"),
            53: ("🌧️", "Moderate Drizzle"),
            55: ("🌧️", "Heavy Drizzle"),
            61: ("🌧️", "Slight Rain"),
            63: ("🌧️", "Moderate Rain"),
            65: ("🌧️", "Heavy Rain"),
            71: ("❄️", "Slight Snow"),
            73: ("❄️", "Moderate Snow"),
            75: ("❄️", "Heavy Snow"),
            77: ("❄️", "Snow Grains"),
            80: ("🌦️", "Slight Rain Showers"),
            81: ("🌦️", "Moderate Rain Showers"),
            82: ("🌦️", "Violent Rain Showers"),
            85: ("❄️", "Slight Snow Showers"),
            86: ("❄️", "Heavy Snow Showers"),
            95: ("⛈️", "Thunderstorm"),
            96: ("⛈️", "Thunderstorm with Hail"),
            99: ("⛈️", "Thunderstorm with Heavy Hail")
        }
        
        icon, desc = weather_icons.get(code, ("☁️", "Cloudy"))
        unit_symbol = "°F" if is_us else "°C"
        return f"{location_str}: {icon} {temp}{unit_symbol} ({desc})"
    except Exception as e:
        print(f"Failed to fetch weather: {e}")
        return None


# Initialize Gemini Client if key is provided
if GEMINI_API_KEY:
    # Explicitly set api_version to 'v1' to avoid v1beta 404 errors
    ai_client = genai.Client(api_key=GEMINI_API_KEY, http_options={'api_version': 'v1'})
    try:
        print("Checking available Gemini models...")
        # Only print first few to keep logs clean
        models = list(ai_client.models.list())
        for m in models[:10]:
            print(f" - Found model: {m.name}")
    except Exception as e:
        print(f"Could not list models: {e}")
else:
    ai_client = None

DEFAULT_STYLE = '''
@page { margin: 5pt; }
body { 
    font-family: "Georgia", "Bookerly", "Malgun Gothic", "Nanum Gothic", serif; 
    line-height: 1.6; 
    margin: 10px; 
    color: #000; 
    background-color: #fff; 
}
h1, h2, h3, h4, h5, h6 { 
    font-family: "Georgia", serif; 
    color: #000; 
    page-break-after: avoid; 
    break-after: avoid; 
}
h1 { text-align: center; font-size: 1.8em; margin-bottom: 0.2em; font-weight: bold; }
.metadata { 
    text-align: center; 
    font-size: 0.95em; 
    color: #000; 
    margin-bottom: 20px; 
    border-top: 1px solid #000;
    border-bottom: 2px solid #000; 
    padding: 8px 0; 
    text-transform: uppercase;
    letter-spacing: 1px;
}
h2 { font-size: 1.4em; border-bottom: 1px solid #000; padding-bottom: 3px; margin-top: 30px; }
h3 { font-size: 1.2em; margin-top: 25px; }
p { 
    margin-bottom: 1.3em; 
    text-align: justify; 
    text-justify: inter-word;
    -webkit-hyphens: auto; 
    -epub-hyphens: auto; 
    hyphens: auto; 
}
img { 
    max-width: 100%; 
    height: auto; 
    display: block; 
    margin: 20px auto; 
}
table { width: 100%; border-collapse: collapse; margin: 15px 0; border: 1px solid #000; }
td { padding: 8px; border-bottom: 1px solid #ccc; }

/* Magazine Style Typography */
.dropcap {
    float: left;
    font-size: 3.2em;
    line-height: 0.8;
    margin: 0.1em 0.15em 0 0;
    color: #000;
    font-weight: bold;
}

.leadin {
    font-weight: bold;
    font-variant: small-caps;
    letter-spacing: 0.5px;
}

.byline {
    text-align: center;
    font-size: 0.9em;
    font-style: italic;
    margin-top: -10px;
    margin-bottom: 20px;
    color: #333;
}

.ornament {
    text-align: center;
    font-size: 1.4em;
    margin: 30px 0;
    color: #000;
}

.web-link-box {
    text-align: center;
    margin: 25px 0;
    font-size: 0.9em;
}
.web-link {
    text-decoration: underline;
    color: #000;
    font-weight: bold;
}

/* Premium Pull-Quotes */
blockquote {
    margin: 25px 20px;
    padding: 12px 10px;
    border-top: 1px solid #000;
    border-bottom: 1px solid #000;
    border-left: none;
    border-right: none;
    background-color: transparent;
    font-style: italic;
    font-size: 1.1em;
    line-height: 1.5;
    text-align: center;
    color: #000;
}

/* E-Ink Optimized Code Blocks */
code, pre {
    font-family: "Courier New", Courier, monospace;
    background-color: transparent;
    border: 1px solid #000;
    font-size: 0.85em;
}

code {
    padding: 2px 4px;
}

pre {
    display: block;
    padding: 12px;
    margin: 15px 0;
    white-space: pre-wrap;       /* css-3 */
    white-space: -moz-pre-wrap;  /* Mozilla, since 1999 */
    white-space: -pre-wrap;      /* Opera 4-6 */
    white-space: -o-pre-wrap;    /* Opera 7 */
    word-wrap: break-word;       /* Internet Explorer 5.5+ */
}

/* AI Summary Box */
.summary-box {
    background-color: transparent;
    border: 1px solid #000;
    padding: 12px;
    margin-bottom: 25px;
}
.summary-title {
    font-weight: bold;
    font-size: 0.9em;
    color: #000;
    margin-bottom: 8px;
    text-transform: uppercase;
    letter-spacing: 1.5px;
    border-bottom: 1px solid #000;
    display: inline-block;
}
.summary-text {
    font-size: 0.95em;
    color: #111;
    margin: 0;
    line-height: 1.5;
}

/* Editorial / Morning Briefing Box */
.editorial-box {
    background-color: transparent;
    border: 4px double #000;
    padding: 15px;
    margin-bottom: 30px;
}
.editorial-title {
    font-weight: bold;
    font-size: 1.1em;
    text-align: center;
    color: #000;
    margin-bottom: 12px;
    text-transform: uppercase;
    letter-spacing: 2px;
    font-family: "Georgia", serif;
}
.editorial-text {
    font-size: 1em;
    color: #000;
    margin: 0;
    line-height: 1.6;
    text-align: justify;
    font-style: italic;
}

/* Dashboard Styles (grouped by newsletter publisher) */
.publisher-section {
    margin-top: 35px;
    margin-bottom: 15px;
}
.publisher-header {
    font-size: 1.4em;
    font-weight: bold;
    border-bottom: 2px solid #000;
    padding-bottom: 3px;
    margin-bottom: 15px;
    text-transform: uppercase;
    letter-spacing: 1px;
    font-family: "Georgia", serif;
}
.dashboard-article {
    margin-bottom: 20px;
    padding-bottom: 10px;
    border-bottom: 1px dashed #ccc;
}
.dashboard-title { font-size: 1.25em; font-weight: bold; margin-bottom: 5px; font-family: "Georgia", serif; }
.dashboard-link { text-decoration: none; color: #000; }
.dashboard-link:hover { text-decoration: underline; }
.dashboard-meta { font-size: 0.85em; color: #333; margin-bottom: 8px; }
.dashboard-summary { font-size: 0.95em; line-height: 1.4; color: #111; font-style: italic; }

/* Navigation Footer */
.nav-footer {
    margin-top: 45px;
    padding-top: 15px;
    border-top: 1px solid #000;
    text-align: center;
    font-size: 1.05em;
}
.nav-link { margin: 0 12px; text-decoration: none; font-weight: bold; color: #000; }
'''

IMAGE_ID_COUNTER = 0

def strip_emojis(text):
    """Remove characters outside the Basic Multilingual Plane (emojis)."""
    if not text: return ""
    return re.sub(r'[^\u0000-\uFFFF]', '', text)

def estimate_reading_time(html_content):
    """Estimate reading time in minutes based on word count (approx 225 WPM)."""
    if not html_content: return 0
    # Strip HTML tags
    text = re.sub('<[^<]+?>', '', html_content)
    # Count words
    word_count = len(text.split())
    # For CJK languages, word count by spaces is inaccurate, but this is a reasonable heuristic for newsletters
    minutes = max(1, round(word_count / 225))
    return minutes

def summarize_content(text, is_korean):
    """Generate a 3-bullet summary and a one-liner cover summary using Gemini API."""
    import json
    if not GEMINI_API_KEY:
        print("Skipping summary: GEMINI_API_KEY not found.")
        return None, None
        
    if not ai_client:
        print("Skipping summary: ai_client not initialized.")
        return None, None

    if not text or len(text) < 300:
        print(f"Skipping summary: Content too short ({len(text) if text else 0} chars).")
        return None, None
    
    # Remove HTML tags for the AI prompt
    clean_text = re.sub('<[^<]+?>', '', text).strip()
    # Truncate if too long
    clean_text = clean_text[:8000]

    lang_instr = "Korean" if is_korean else "English"
    print(f"Requesting AI summary for {lang_instr} article ({len(clean_text)} chars)...")
    
    prompt = f"""
    You are an expert editor for a daily newsletter digest.
    Read the following newsletter article and provide:
    1. A short, punchy one-line summary (maximum 75 characters) of the MAIN TOPIC or KEY NEWS story discussed in this issue. It should read like a newspaper front-page headline (e.g., "Tech stocks rally as inflation cools" or "Scientists discover new memory pathway").
       CRITICAL: Do NOT write generic titles like "Morning Brew newsletter", "Weekly digest", or mention the name of the newsletter or the date. Focus on the actual news content.
    2. A detailed summary of the article in exactly 3 bullet points.
    
    Output your response in JSON format with these exact keys:
    {{
        "one_liner": "your cover one-liner summary here",
        "bullets": [
            "bullet point 1",
            "bullet point 2",
            "bullet point 3"
        ]
    }}
    
    IMPORTANT: Provide both summaries in {lang_instr} because the original text is in {lang_instr}.
    
    Text: {clean_text}
    """
    
    def parse_json_response(text_out):
        try:
            # Clean text from potential markdown wrap
            clean_json = text_out.strip()
            if clean_json.startswith("```json"):
                clean_json = clean_json[7:]
            elif clean_json.startswith("```"):
                clean_json = clean_json[3:]
            if clean_json.endswith("```"):
                clean_json = clean_json[:-3]
            clean_json = clean_json.strip()
            
            data = json.loads(clean_json)
            one_liner = data.get("one_liner", "").strip()
            bullets_list = data.get("bullets", [])
            bullets = "\n".join([f"• {b}" for b in bullets_list if b.strip()])
            
            one_liner = one_liner.strip('"\'[] ')
            return bullets, one_liner
        except Exception as e:
            print(f"JSON parsing failed: {e}. Attempting regex fallback...")
            # Fallback regex parsing
            one_liner = ""
            lines = [line.strip("•-* ") for line in text_out.split('\n') if line.strip()]
            for line in lines:
                if "one_liner" in line.lower() or "headline" in line.lower():
                    one_liner = line.split(":", 1)[-1].strip()
            if not one_liner and lines:
                one_liner = lines[0]
            bullets = "\n".join([f"• {l}" for l in lines if l != one_liner][:3])
            return bullets, one_liner

    try:
        response = ai_client.models.generate_content(
            model="gemini-2.0-flash",
            contents=prompt,
            config={'response_mime_type': 'application/json'}
        )
        if response and response.text:
            print("Successfully generated AI summary.")
            return parse_json_response(response.text)
    except Exception as e:
        print(f"Gemini gemini-2.0-flash failed: {e}. Trying fallback model...")
        try:
            response = ai_client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt,
                config={'response_mime_type': 'application/json'}
            )
            if response and response.text:
                print("Successfully generated AI summary (2.5 fallback).")
                return parse_json_response(response.text)
        except Exception as e2:
            print(f"Gemini fallback failed: {e2}")
    return None, None

def generate_editorial(articles):
    """Generate a synthesized daily editorial briefing from all articles."""
    if not GEMINI_API_KEY or not ai_client:
        print("Skipping editorial: Gemini API not available.")
        return None
        
    if not articles:
        return None
        
    print(f"Generating daily editorial briefing from {len(articles)} articles...")
    
    article_briefs = []
    has_korean = False
    for art in articles:
        title = art['title']
        publisher = art['publisher']
        summary = art['summary'] or (art['content'][:500] + "...")
        article_briefs.append(f"Publisher: {publisher}\nTitle: {title}\nSummary:\n{summary}\n")
        if art['is_korean']:
            has_korean = True
            
    articles_input = "\n---\n".join(article_briefs)
    lang_instr = "Korean" if has_korean else "English"
    
    prompt = f"""
    You are the Editor-in-Chief of a customized daily newsletter digest.
    Read the summaries of today's incoming newsletters:
    
    {articles_input}
    
    Write a short, professional editorial/morning briefing (around 100-150 words) synthesizing the most interesting themes, trends, or major highlights from today's issues.
    Start directly with the content. Avoid greetings like "Here is your briefing" or "As the editor-in-chief". Just write a clean, journalistic briefing/editorial.
    IMPORTANT: Provide the briefing in {lang_instr} because the main reader reads in {lang_instr}.
    """
    
    try:
        response = ai_client.models.generate_content(
            model="gemini-2.0-flash",
            contents=prompt
        )
        if response and response.text:
            print("Successfully generated daily editorial.")
            return response.text.strip()
    except Exception as e:
        print(f"Failed to generate editorial: {e}")
        try:
            response = ai_client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt
            )
            if response and response.text:
                print("Successfully generated daily editorial (2.5 fallback).")
                return response.text.strip()
        except Exception as e2:
            print(f"Failed to generate editorial with fallback: {e2}")
    return None


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
            
    from_parts = email.header.decode_header(from_header)
    decoded_from = ""
    for part, encoding in from_parts:
        if isinstance(part, bytes):
            decoded_from += part.decode(encoding or 'utf-8', errors='ignore')
        else:
            decoded_from += part
    
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

    return decoded_subject, decoded_from, html_content, cid_images

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
                
                # Convert to Grayscale ('L' mode) for optimal e-ink rendering and file size
                pil_img = pil_img.convert("L")
                
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

def extract_view_in_browser_link(raw_html):
    """Find a 'view in browser' or similar web version link in the HTML."""
    if not raw_html:
        return None
    try:
        parser = html.HTMLParser(encoding='utf-8')
        tree = html.fromstring(raw_html.encode('utf-8'), parser=parser)
        noise_patterns = ['view in browser', 'view online', 'web version', 'read online', 'view it in your browser', 'read in browser', 'read on subhead', 'view in your browser']
        for link in tree.xpath('//a'):
            text = link.text_content().lower()
            href = link.get('href')
            if href and any(p in text for p in noise_patterns):
                return href
    except Exception as e:
        print(f"Failed to extract web version link: {e}")
    return None

def extract_newsletter_name(sender):
    """Extract display name of the sender, or fall back to domain/username."""
    if not sender:
        return "Unknown Newsletter"
    
    # Check if we have display name like: Morning Brew <news@morningbrew.com>
    if '<' in sender:
        match = re.match(r'^([^<]+)', sender)
        if match:
            name = match.group(1).strip()
            name = name.strip('"\'')
            if name:
                return name
                
    # Fall back to parsing the email address
    email_match = re.search(r'<([^>]+)>', sender)
    email_addr = email_match.group(1).strip() if email_match else sender.strip()
    if '@' in email_addr:
        domain = email_addr.split('@')[-1]
        parts = domain.split('.')
        if len(parts) > 1:
            name = parts[-2] if parts[-2] not in ('co', 'com', 'org', 'net', 'edu', 'gov') or len(parts) < 3 else parts[-3]
            return name.capitalize()
        return domain.capitalize()
    return sender

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
            
            # Extract web version link before any cleaning is applied
            web_version_url = extract_view_in_browser_link(raw_html)
            
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
                parser = html.HTMLParser(encoding='utf-8')
                tree = html.fromstring(content.encode('utf-8'), parser=parser)
                tree = advanced_cleanup(tree)
                content = html.tostring(tree, encoding='unicode', method='html')
            
            content = strip_emojis(content)
            title = strip_emojis(title)
            
            is_korean = bool(re.search('[\u3131-\u3163\uac00-\ud7a3]+', content))
            
            summary, one_liner = summarize_content(content, is_korean)
            
            articles.append({
                'title': title,
                'content': content,
                'cid_images': cid_images,
                'sender': sender,
                'publisher': extract_newsletter_name(sender),
                'summary': summary,
                'one_liner': one_liner or title,
                'is_korean': is_korean,
                'reading_time': estimate_reading_time(content),
                'web_version_url': web_version_url
            })
            if not DEBUG:
                client.copy(msgid, PROCESSED_LABEL)
                client.delete_messages(msgid)
        if not DEBUG:
            client.expunge()
    return articles

def draw_wrapped_text(draw, text, font, x, y, max_width, line_height, color=0):
    """Draw wrapped text and return the ending Y coordinate."""
    words = text.split()
    lines = []
    current_line = []
    for word in words:
        current_line.append(word)
        line_text = " ".join(current_line)
        w = draw.textlength(line_text, font=font) if hasattr(draw, 'textlength') else len(line_text) * 8
        if w > max_width:
            current_line.pop()
            lines.append(" ".join(current_line))
            current_line = [word]
    if current_line:
        lines.append(" ".join(current_line))
        
    for line in lines:
        draw.text((x, y), line, fill=color, font=font)
        y += line_height
    return y

def generate_cover_image(title, date_str, weather_info=None, editorial_text=None, articles=None):
    """Generate a classic daily newspaper front-page cover image for the EPUB."""
    width, height = 600, 800
    # Cream paper background
    image = Image.new('RGB', (width, height), color=(248, 246, 240))
    draw = ImageDraw.Draw(image)
    
    # Detect if any text to be rendered contains CJK (Korean) characters
    has_korean = (
        bool(re.search('[\u3131-\u3163\uac00-\ud7a3]+', date_str)) or
        (weather_info and bool(re.search('[\u3131-\u3163\uac00-\ud7a3]+', weather_info))) or
        (editorial_text and bool(re.search('[\u3131-\u3163\uac00-\ud7a3]+', editorial_text))) or
        (articles and any(bool(re.search('[\u3131-\u3163\uac00-\ud7a3]+', art['one_liner'])) for art in articles))
    )
    
    # Prioritize CJK-supporting fonts if Korean is present
    font_paths = []
    if has_korean:
        print("Korean characters detected on cover page. Prioritizing CJK fonts...")
        font_paths.extend([
            # Ubuntu Nanum Myeongjo (Beautiful Serif CJK)
            "/usr/share/fonts/truetype/nanum/NanumMyeongjo.ttf",
            # Ubuntu Nanum Gothic (Sans CJK)
            "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
            # macOS Apple SD Gothic Neo
            "/System/Library/Fonts/AppleSDGothicNeo.ttc",
            # macOS AppleGothic
            "/System/Library/Fonts/Supplemental/AppleGothic.ttf"
        ])
        
    font_paths.extend([
        "/System/Library/Fonts/Times.ttc",                      # macOS Times
        "/System/Library/Fonts/Supplemental/Georgia.ttf",       # macOS Georgia
        "/Library/Fonts/Georgia.ttf",                           # macOS Georgia Supplemental
        "/usr/share/fonts/truetype/liberation/LiberationSerif-Bold.ttf", # Ubuntu/Linux
        "georgia.ttf",
        "times.ttf",
        "arial.ttf"
    ])
    
    title_font = None
    meta_font = None
    sub_font = None
    ornament_font = None
    
    for path in font_paths:
        try:
            if os.path.exists(path) or path in ["georgia.ttf", "times.ttf", "arial.ttf"]:
                title_font = ImageFont.truetype(path, 52)
                meta_font = ImageFont.truetype(path, 16)
                sub_font = ImageFont.truetype(path, 22)
                ornament_font = ImageFont.truetype(path, 36)
                break
        except:
            continue
            
    if not title_font:
        title_font = ImageFont.load_default()
        meta_font = ImageFont.load_default()
        sub_font = ImageFont.load_default()
        ornament_font = ImageFont.load_default()

    # Draw border around the page
    margin = 25
    draw.rectangle([margin, margin, width - margin, height - margin], outline=(0, 0, 0), width=2)
    
    # --- MASTHEAD ---
    # Draw double rules at the top of masthead
    masthead_top = 80
    draw.line([margin + 15, masthead_top, width - margin - 15, masthead_top], fill=(0, 0, 0), width=4)
    
    # Main Newspaper Title
    title_text = "THE DAILY DIGEST"
    title_w = draw.textlength(title_text, font=title_font) if hasattr(draw, 'textlength') else 350
    draw.text(((width - title_w)/2, masthead_top + 25), title_text, fill=(0, 0, 0), font=title_font)
    
    # Draw rules for the metadata bar
    bar_top = masthead_top + 105
    bar_height = 36
    draw.line([margin + 15, bar_top, width - margin - 15, bar_top], fill=(0, 0, 0), width=2)
    draw.line([margin + 15, bar_top + bar_height, width - margin - 15, bar_top + bar_height], fill=(0, 0, 0), width=2)
    
    # Metadata Text
    day_of_year = datetime.date.today().strftime("%j")
    vol_str = f"VOL. I  NO. {day_of_year}"
    
    # Left aligned Volume
    draw.text((margin + 25, bar_top + 10), vol_str, fill=(0, 0, 0), font=meta_font)
    
    # Right aligned Date
    date_w = draw.textlength(date_str, font=meta_font) if hasattr(draw, 'textlength') else 100
    draw.text((width - margin - 25 - date_w, bar_top + 10), date_str, fill=(0, 0, 0), font=meta_font)
    
    # --- WEATHER BAR ---
    weather_y = bar_top + bar_height + 15
    if weather_info:
        weather_clean = weather_info.replace("☀️", "").replace("🌤️", "").replace("⛅", "").replace("☁️", "").replace("🌫️", "").replace("🌧️", "").replace("❄️", "").replace("🌦️", "").replace("⛈️", "").strip()
        weather_text = f"Today's Weather: {weather_clean}"
        weather_w = draw.textlength(weather_text, font=meta_font) if hasattr(draw, 'textlength') else 150
        draw.text(((width - weather_w)/2, weather_y), weather_text, fill=(0, 0, 0), font=meta_font)
        draw.line([margin + 15, weather_y + 25, width - margin - 15, weather_y + 25], fill=(0, 0, 0), width=1)
        start_y = weather_y + 40
    else:
        start_y = bar_top + bar_height + 25
        
    # --- DAILY EDITORIAL BRIEFING ---
    if editorial_text:
        ed_title = "EDITORIAL BRIEFING"
        ed_title_w = draw.textlength(ed_title, font=meta_font) if hasattr(draw, 'textlength') else 150
        draw.text(((width - ed_title_w)/2, start_y), ed_title, fill=(0, 0, 0), font=meta_font)
        
        ed_y = start_y + 25
        end_ed_y = draw_wrapped_text(draw, editorial_text, meta_font, margin + 30, ed_y, width - 2 * margin - 60, 24)
        
        # Draw frame around editorial
        draw.rectangle([margin + 15, start_y - 10, width - margin - 15, end_ed_y + 10], outline=(0, 0, 0), width=1)
        start_y = end_ed_y + 35
        
    # --- TODAY'S HEADLINES / IN THIS EDITION ---
    if articles:
        toc_title = "IN THIS EDITION"
        toc_title_w = draw.textlength(toc_title, font=meta_font) if hasattr(draw, 'textlength') else 120
        draw.text(((width - toc_title_w)/2, start_y), toc_title, fill=(0, 0, 0), font=meta_font)
        draw.line([margin + 15, start_y + 25, width - margin - 15, start_y + 25], fill=(0, 0, 0), width=1)
        
        bullet_y = start_y + 40
        for art in articles[:6]:  # Show at most 6 articles to fit cover height
            pub = art['publisher']
            one_liner = art['one_liner']
            bullet_text = f"• {pub}: {one_liner}"
            bullet_y = draw_wrapped_text(draw, bullet_text, meta_font, margin + 30, bullet_y, width - 2 * margin - 60, 24)
            bullet_y += 8
            
        start_y = bullet_y + 15

    # Flourish at bottom
    flourish = "❖   ❖   ❖"
    flourish_w = draw.textlength(flourish, font=ornament_font) if hasattr(draw, 'textlength') else 100
    flourish_y = max(start_y, 730)
    if flourish_y < height - margin - 10:
        draw.text(((width - flourish_w)/2, flourish_y), flourish, fill=(0, 0, 0), font=ornament_font)

    # Convert to grayscale
    image = image.convert("L")

    img_byte_arr = io.BytesIO()
    image.save(img_byte_arr, format='JPEG', quality=90)
    return img_byte_arr.getvalue()
def apply_dropcap(content, is_korean):
    """Apply dropcap to the first letter and lead-in styling to the next 3 words of English articles."""
    if is_korean: return content
    
    try:
        parser = html.HTMLParser(encoding='utf-8')
        tree = html.fromstring(content.encode('utf-8'), parser=parser)
        
        # Find the first paragraph with text
        for p in tree.xpath('//p'):
            text = p.text_content().strip()
            if text and text[0].isalpha():
                raw_p = html.tostring(p, encoding='unicode')
                # Find the first letter after <p...>
                match = re.search(r'(<p[^>]*>)(?:\s*)([a-zA-Z])', raw_p)
                if match:
                    remaining_text = raw_p[match.end(2):]
                    # Match the first 3 words (consisting of non-space chars separated by spaces, stopping at tag start '<')
                    words_match = re.match(r'^((?:\s*[^\s<]+){1,3})', remaining_text)
                    if words_match:
                        lead_words = words_match.group(1)
                        new_p = raw_p[:match.start(2)] + f'<span class="dropcap">{match.group(2)}</span><span class="leadin">{lead_words}</span>' + remaining_text[words_match.end(1):]
                    else:
                        new_p = raw_p[:match.start(2)] + f'<span class="dropcap">{match.group(2)}</span>' + remaining_text
                        
                    new_p_node = html.fromstring(new_p)
                    p.getparent().replace(p, new_p_node)
                    break
        return html.tostring(tree, encoding='unicode', method='html')
    except Exception as e:
        print(f"Dropcap/Leadin failed: {e}")
        return content

def create_epub(articles):
    date_str = datetime.date.today().strftime("%B %d, %Y")
    filename = f"Daily_Digest_{datetime.date.today().isoformat()}.epub"
    
    book = epub.EpubBook()
    book.set_identifier(f'digest-{datetime.date.today().isoformat()}')
    book.set_title(f'Daily Digest - {date_str}')
    
    has_korean = any(art['is_korean'] for art in articles)
    book.set_language('ko' if has_korean else 'en')
    
    # Fetch weather info if location is provided
    weather_info = fetch_weather(USER_LOCATION)
    
    # Generate the Editorial Briefing using Gemini
    editorial_text = generate_editorial(articles)
    
    # Set Cover (passing weather, editorial text, and articles dynamically to generate a rich front page)
    if os.path.exists('cover.jpg'):
        with open('cover.jpg', 'rb') as f:
            book.set_cover("cover.jpg", f.read())
    else:
        cover_content = generate_cover_image("Daily Digest", date_str, weather_info, editorial_text, articles)
        book.set_cover("cover.jpg", cover_content)
    
    style_item = epub.EpubItem(uid="style_default", file_name="style/default.css", media_type="text/css", content=DEFAULT_STYLE)
    book.add_item(style_item)

    # Note: We are skipping the daily dashboard page since the cover page now contains 
    # both the weather, editorial briefing, and index of articles.
 
    chapters = []
    for i, art in enumerate(articles):
        is_korean = art['is_korean']
        
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
 
        # Apply typography improvements
        processed_content = apply_dropcap(processed_content, is_korean)
 
        # Build smart metadata header
        sender_match = re.search(r'([^<]+)', art['sender'])
        sender_name = sender_match.group(1).strip() if sender_match else art['sender']
        
        # Prepare Summary Section
        summary_html = ""
        if art['summary']:
            sum_title = "요약" if is_korean else "Key Takeaways"
            formatted_summary = art['summary'].replace('\n', '<br/>')
            summary_html = f"""
                <div class="summary-box">
                    <div class="summary-title">{sum_title}</div>
                    <div class="summary-text">{formatted_summary}</div>
                </div>
            """
            
        web_link_html = ""
        if art.get('web_version_url'):
            web_link_html = f"""
                <div class="web-link-box">
                    <a href="{art['web_version_url']}" class="web-link">🔗 View Original Web Version</a>
                </div>
            """
 
        # Navigation Footer
        prev_link = f'<a href="chap_{i-1}.xhtml" class="nav-link">← Previous Article</a>' if i > 0 else ''
        next_link = f'<a href="chap_{i+1}.xhtml" class="nav-link">Next Article →</a>' if i < len(articles) - 1 else ''
        nav_footer = f"""
            <div class="nav-footer">
                {prev_link}
                {next_link}
            </div>
        """
 
        chapter = epub.EpubHtml(title=art['title'], file_name=f'text/chap_{i}.xhtml', lang='ko' if is_korean else 'en')
        chapter.content = f"""
            <h1>{art['title']}</h1>
            <div class="byline">By {art['publisher']}</div>
            <div class="metadata">
                {art['reading_time']} min read
            </div>
            {summary_html}
            <div>{processed_content}</div>
            {web_link_html}
            {nav_footer}
        """
        chapter.add_item(style_item)
        
        book.add_item(chapter)
        chapters.append(chapter)
 
    book.toc = tuple(chapters)
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    
    # Prepend cover to the spine so swipe-reading starts from the cover page
    book.spine = ['cover', 'nav'] + chapters
    
    # Configure EPUB Guide / Landmarks for Kindle native menu navigation
    book.guide.append({'type': 'cover', 'title': 'Cover Page', 'href': 'cover.xhtml'})
    book.guide.append({'type': 'toc', 'title': 'Table of Contents', 'href': 'nav.xhtml'})
    if chapters:
        book.guide.append({'type': 'text', 'title': 'Beginning', 'href': chapters[0].file_name})
        
    epub.write_epub(filename, book, options={'epub3_landmark': True, 'landmark_title': 'Table of Contents'})
    return filename

def send_emails(filepath):
    recipients = [KINDLE_EMAIL]
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
