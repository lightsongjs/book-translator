# MS7 Book Translation Project

## Overview
A Python-based system that translates EPUB books from English to Romanian. The system splits books into chapters and segments, manages translation workflow, tracks progress, and reassembles translated content.

## Project Structure

```
ms7/
├── 00_en_full_epub/       # Original English EPUB
├── 01_en_chapters/        # English chapters (extracted)
├── 02_en_segments/        # English segments (split for translation)
├── 03_ro_segments/        # Romanian translated segments
├── 04_ro_chapters/        # Romanian reassembled chapters
├── 05_ro_full_epub/       # Final Romanian EPUB
├── 06_tracking/           # Progress tracking files
├── 07_backup/             # Backup files
├── book_translator.py     # Main translation system
├── translation_config.json # Project configuration
└── MS7.epub              # Final translated book
```

## Configuration

### translation_config.json
```json
{
  "book_name": "MS7",
  "epub_file": "MS7.epub",
  "source_language": "en",
  "target_language": "ro",
  "created": "2025-08-23T16:22:15.848636",
  "project_dir": "."
}
```

### .env (Email Credentials)
Create a `.env` file in the project root with your Mailjet credentials:

```bash
# Mailjet Email Configuration
MAILJET_API_KEY=your_api_key_here
MAILJET_SECRET_KEY=your_secret_key_here
MAILJET_SENDER_EMAIL=your_verified_email@example.com
MAILJET_SENDER_NAME=MS7 Translator
KINDLE_EMAIL=yourname@kindle.com
```

**Important:**
- `.env` is in `.gitignore` to keep credentials secure
- Use `.env.example` as a template
- Never commit `.env` to version control

## Main Commands

### Translation Workflow
```bash
# Initialize project with EPUB
python book_translator.py --init <epub_file>

# Extract chapters from EPUB
python book_translator.py --extract-chapters

# Split all chapters into segments
python book_translator.py --split-all-chapters

# Split specific chapter
python book_translator.py --split-chapter <chapter_number>

# Combine translated segments into chapter
python book_translator.py --combine-chapter <chapter_number>

# Combine all translated chapters
python book_translator.py --combine-all-chapters

# Create final Romanian EPUB
python book_translator.py --create-epub
```

### Progress & Statistics
```bash
# Show overall progress
python book_translator.py --progress

# Generate statistics for all chapters
python book_translator.py --statistics all

# Generate statistics for specific chapter
python book_translator.py --statistics <chapter_number>
```

### Utilities
```bash
# Open chapter in editor
python book_translator.py --open-chapter <chapter_number>

# Verify translation (line counts, first/last lines)
python book_translator.py --verify <chapter_number>

# Compare English and Romanian versions side-by-side
python book_translator.py --compare <chapter_number>

# Quick check without spoilers (first 10 words per segment)
python book_translator.py --quick-check <chapter_number>

# Backup progress for chapter
python book_translator.py --backup <chapter_number>

# Extract table of contents
python book_translator.py --extract-toc
```

### Send to Kindle
```bash
# Send any file to Kindle (EPUB, MD → auto-converts to TXT, TXT)
python book_translator.py --sendtokindle <file_path>

# Examples:
python book_translator.py --sendtokindle MS7.epub
python book_translator.py --sendtokindle The_Devils_-_Joe_Abercrombie.epub
python book_translator.py --sendtokindle 04_ro_chapters/49_CHAPTER_47_ro.md  # Auto-converts to TXT

# Note: MD files are automatically converted to TXT (Kindle compatible)
# Temporary TXT file is created, sent, then deleted
```

## Kindle Email Setup

### Prerequisites
1. **Mailjet Account** (free tier: 200 emails/day)
   - Sign up at https://www.mailjet.com
   - Get API Key and Secret Key from Account Settings → API Key Management
   - Verify your sender email address

2. **Amazon Kindle Settings**
   - Go to amazon.com → Manage Your Content and Devices
   - Preferences → Personal Document Settings
   - Add your Mailjet sender email to "Approved Personal Document E-mail List"
   - Note your Kindle email (format: yourname@kindle.com)

3. **Create `.env` file** with your credentials (see Configuration section above)

### Supported File Types
- **EPUB** - Automatically converted by Kindle
- **MD** (Markdown) - Sent as plain text
- **TXT** - Plain text files

### Email Service Options
- **Mailjet** - 200 emails/day (free, permanent)
- **Brevo** (Sendinblue) - 300 emails/day (free, permanent)
- **SendGrid** - 100 emails/day (free, permanent)

## Dependencies

```bash
# Install all dependencies
uv pip install click rich ebooklib beautifulsoup4 pandas mailjet_rest python-dotenv

# Or using pip
pip install click rich ebooklib beautifulsoup4 pandas mailjet_rest python-dotenv
```

## Environment Setup

```bash
# Create virtual environment
uv venv

# Activate virtual environment
source .venv/bin/activate  # Linux/Mac
.venv\Scripts\activate     # Windows

# Install dependencies
uv pip install click rich ebooklib beautifulsoup4 pandas mailjet_rest python-dotenv

# Create .env file from template
cp .env.example .env
# Then edit .env with your actual credentials
```

## Key Features

### Translation Management
- Splits EPUB into manageable segments (max 1500 words)
- Tracks progress with detailed statistics
- Prevents content loss with backup system
- Validates translation quality (word count ratios)

### Progress Tracking
- Real-time progress monitoring
- Detailed statistics per chapter
- Translation quality metrics
- Backup and restore functionality

### Kindle Integration
- Direct send to Kindle via email
- Supports multiple file formats
- Configurable email service (Mailjet/Brevo/SendGrid)
- Automatic file encoding and attachment

## Workflow Examples

### Initial Setup
```bash
# 1. Setup project
python book_translator.py --init MyBook.epub

# 2. Extract and split
python book_translator.py --extract-chapters
python book_translator.py --split-all-chapters
```

### Manual Translation Workflow (Per Chapter)

#### Automated Workflow (Recommended)

**Linux/WSL:**
```bash
./translate_next.sh
```

**Windows:**
```cmd
translate_next.bat
```

**What it does:**
1. Auto-detects next chapter to translate (highest + 1)
2. Opens chapter for editing with `--open-chapter`
3. Waits for you to finish translation (press any key)
4. Auto-combines segments with `--combine-chapter`
5. Auto-verifies translation:
   - Shows line counts (EN vs RO)
   - Checks ratio (0.7-1.3 acceptable range)
   - If ratio fails: offers to open `--compare` for side-by-side review
6. Asks: "Trimitem pe Kindle? (y/n)"
7. If yes: converts MD → TXT and sends to Kindle
8. Clean, minimal output - only essential info

**Features:**
- ✅ Automatic chapter detection
- ✅ Ratio validation (warns if outside 0.7-1.3)
- ✅ Optional file comparison on error
- ✅ MD files auto-converted to TXT for Kindle
- ✅ Temp files cleaned up automatically
- ✅ UTF-8 support for Romanian characters (ă, î, ș, ț, â)

#### Manual Workflow (Step by Step)
```bash
# 1. Open chapter for translation
python book_translator.py --open-chapter 47

# 2. Translate manually in your editor
# (Edit segments in 03_ro_segments/)

# 3. Combine translated segments
python book_translator.py --combine-chapter 47

# 4. Quick verification (console)
python book_translator.py --verify 47

# 5. Visual comparison (optional)
python book_translator.py --compare 47
```

### Final Assembly
```bash
# Combine all translated chapters
python book_translator.py --combine-all-chapters

# Create final Romanian EPUB
python book_translator.py --create-epub

# Send to Kindle
python book_translator.py --sendtokindle MyBook_ro.epub
```

## Notes

- All operations preserve content integrity
- Configuration stored in `translation_config.json`
- Progress tracked in `06_tracking/translation_log.json`
- Backups automatically created in `07_backup/`
- Email credentials stored securely in `.env` file (not committed to git)

## Troubleshooting

### Kindle Email Issues
- Ensure sender email is in Amazon approved list
- Check `.env` file exists and contains all required variables
- Check Mailjet API keys are correct in `.env`
- Verify Kindle email address format
- Check Mailjet account is verified

### Translation Issues
- Check word count ratios with `--statistics`
- Compare versions with `--compare`
- Restore from backup if needed
- Use `--quick-check` to preview without spoilers
