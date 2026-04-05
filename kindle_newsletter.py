import os
import datetime
import smtplib
from email.message import EmailMessage
from imapclient import IMAPClient
from readability import Document
from ebooklib import epub

# --- Configuration ---
GMAIL_USER = os.getenv('GMAIL_USER')
GMAIL_APP_PASS = os.getenv('GMAIL_APP_PASSWORD')
KINDLE_EMAIL = os.getenv('KINDLE_EMAIL')
SOURCE_LABEL = 'Daily-Digest'
PROCESSED_LABEL = 'Daily-Digest/Processed'

def clean_html(raw_html):
    """Extracts the main content using Readability."""
    doc = Document(raw_html)
    return doc.title(), doc.summary()

def get_email_content(msg_data):
    """Parses email parts to find HTML content."""
    import email
    msg = email.message_from_bytes(msg_data)
    content = ""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                content = part.get_payload(decode=True).decode(part.get_content_charset() or 'utf-8')
                break
    else:
        content = msg.get_payload(decode=True).decode(msg.get_content_charset() or 'utf-8')
    return content

def process_newsletters():
    articles = []
    with IMAPClient('imap.gmail.com') as client:
        client.login(GMAIL_USER, GMAIL_APP_PASS)
        
        # Ensure the processed folder exists
        if not client.folder_exists(PROCESSED_LABEL):
            client.create_folder(PROCESSED_LABEL)
            
        client.select_folder(SOURCE_LABEL)
        messages = client.search(['NOT', 'DELETED'])
        
        if not messages:
            print("No new newsletters found.")
            return None

        for msgid, data in client.fetch(messages, 'RFC822').items():
            raw_html = get_email_content(data[b'RFC822'])
            title, clean_body = clean_html(raw_html)
            articles.append({'title': title, 'content': clean_body})
            
            # Label as Processed: Copy to subfolder and delete from main folder
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

    chapters = []
    for i, art in enumerate(articles):
        chapter = epub.EpubHtml(title=art['title'], file_name=f'chap_{i}.xhtml', lang='en')
        chapter.content = f"<h1>{art['title']}</h1>{art['content']}"
        book.add_item(chapter)
        chapters.append(chapter)

    book.toc = tuple(chapters)
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.spine = ['nav'] + chapters
    
    epub.write_epub(filename, book)
    return filename

def send_emails(filepath):
    # To Kindle and Original Email
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
    # Ensure environment variables are present
    if not all([GMAIL_USER, GMAIL_APP_PASS, KINDLE_EMAIL]):
        print("Error: Missing environment variables GMAIL_USER, GMAIL_APP_PASSWORD, or KINDLE_EMAIL.")
        exit(1)
        
    articles = process_newsletters()
    if articles:
        epub_file = create_epub(articles)
        send_emails(epub_file)
    else:
        print("Nothing to process today.")
