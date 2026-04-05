# Kindle Daily Newsletter Digest

This tool automatically fetches newsletters from a Gmail label, cleans them for readability, bundles them into an EPUB, and sends them to your Kindle.

## Setup Instructions

1. **Gmail Configuration**:
   - Create a label in Gmail named `Daily-Digest`.
   - Set up filters to automatically move newsletters to this label.
   - Generate an **App Password** in your Google Account settings.

2. **Kindle Configuration**:
   - Go to Amazon -> Manage Your Content and Devices -> Preferences -> Personal Document Settings.
   - Add your Gmail address to the **Approved Personal Document E-mail List**.
   - Note your Kindle's email address (e.g., `yourname@kindle.com`).

3. **GitHub Secrets**:
   In your GitHub repository, go to **Settings -> Secrets and variables -> Actions** and add:
   - `GMAIL_USER`: Your full Gmail address.
   - `GMAIL_APP_PASSWORD`: The 16-character App Password.
   - `KINDLE_EMAIL`: Your Kindle's email address.

## How it Works

The GitHub Action runs every morning at 6:00 AM UTC. It:
1. Connects to your Gmail.
2. Finds emails in the `Daily-Digest` label.
3. Extracts the text and cleans it.
4. Moves the processed emails to `Daily-Digest/Processed`.
5. Creates an EPUB and emails it to you and your Kindle.
