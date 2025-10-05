#!/usr/bin/env python3
"""
Book Translation System - Complete Implementation

A Python-based system that splits EPUB books into chapters, then into manageable 
segments for translation, tracks progress, and reassembles translated content.
NEVER loses any content, even if final segments are very small.
"""

import os
import sys
import json
import csv
import re
import shutil

# Fix Windows console encoding for Romanian characters
if sys.platform == 'win32':
    import codecs
    if sys.stdout.encoding != 'utf-8':
        sys.stdout = codecs.getwriter('utf-8')(sys.stdout.buffer, 'ignore')
    if sys.stderr.encoding != 'utf-8':
        sys.stderr = codecs.getwriter('utf-8')(sys.stderr.buffer, 'ignore')
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Tuple, Optional
import click
from rich.console import Console
from rich.progress import Progress, TextColumn, BarColumn, TaskProgressColumn
from rich.table import Table
from rich.panel import Panel
import ebooklib
from ebooklib import epub
from bs4 import BeautifulSoup
import pandas as pd
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configuration Constants (Hard-coded)
MAX_WORDS_PER_SEGMENT = 1500
MIN_WORDS_INTERMEDIATE = 800
SUPPORTED_FORMATS = ['.epub', '.md', '.txt']
RATIO_WARNING_LOW = 0.8
RATIO_WARNING_HIGH = 1.5
RATIO_ERROR_THRESHOLD = 0.5


console = Console(legacy_windows=False, force_terminal=True)

class BookTranslator:
    def __init__(self, project_dir: str = None):
        self.project_dir = Path(project_dir) if project_dir else Path.cwd()
        self.config_file = self.project_dir / "translation_config.json"
        self.log_file = self.project_dir / "06_tracking" / "translation_log.json"
        
        # Directory structure
        self.dirs = {
            '00_en_full_epub': self.project_dir / '00_en_full_epub',
            '01_en_chapters': self.project_dir / '01_en_chapters',
            '02_en_segments': self.project_dir / '02_en_segments',
            '03_ro_segments': self.project_dir / '03_ro_segments',
            '04_ro_chapters': self.project_dir / '04_ro_chapters',
            '05_ro_full_epub': self.project_dir / '05_ro_full_epub',
            '06_tracking': self.project_dir / '06_tracking',
            '07_backup': self.project_dir / '07_backup'
        }

    def count_words(self, text: str) -> int:
        """Count words in text, handling various whitespace and punctuation."""
        if not text or not isinstance(text, str):
            return 0
        # Remove HTML tags if present
        text = re.sub(r'<[^>]+>', '', text)
        # Count words using word boundaries
        return len(re.findall(r'\b\w+\b', text))

    def validate_project_structure(self) -> bool:
        """Validate that all required directories exist."""
        missing_dirs = []
        for dir_name, dir_path in self.dirs.items():
            if not dir_path.exists():
                missing_dirs.append(dir_name)
        
        if missing_dirs:
            console.print(f"[red]Missing directories: {', '.join(missing_dirs)}[/red]")
            console.print("[yellow]Run --init to create project structure[/yellow]")
            return False
        return True

    def validate_segment_integrity(self, chapter_number: int) -> Tuple[bool, List[str]]:
        """Validate that all content is preserved in segments."""
        warnings = []
        
        # Load original chapter
        log = self.load_log()
        chapter_key = str(chapter_number)
        
        if chapter_key not in log['chapters']:
            return False, [f"Chapter {chapter_number} not found in log"]
        
        chapter_info = log['chapters'][chapter_key]
        original_file = self.dirs['01_en_chapters'] / chapter_info['filename']
        
        if not original_file.exists():
            return False, [f"Original chapter file not found: {original_file}"]
        
        with open(original_file, 'r', encoding='utf-8') as f:
            original_content = f.read()
        
        # Remove header if present
        lines = original_content.split('\n')
        if lines[0].startswith('# Chapter'):
            original_content = '\n'.join(lines[2:])
        
        original_words = self.count_words(original_content)
        
        # Load all segments
        en_pattern = f"*Chapter_{chapter_number}_seg*_of_*.md"
        segment_files = sorted(list(self.dirs['02_en_segments'].glob(en_pattern)))
        
        if not segment_files:
            return False, [f"No segments found for chapter {chapter_number}"]
        
        total_segment_words = 0
        segment_contents = []
        
        for seg_file in segment_files:
            with open(seg_file, 'r', encoding='utf-8') as f:
                content = f.read()
            segment_contents.append(content)
            total_segment_words += self.count_words(content)
        
        # Check word count preservation
        word_diff = abs(original_words - total_segment_words)
        if word_diff > 5:  # Allow small variance
            warnings.append(f"Word count mismatch: original {original_words}, segments {total_segment_words}")
        
        # Check content preservation (basic)
        combined_segments = '\n\n'.join(segment_contents)
        if self.count_words(combined_segments) != total_segment_words:
            warnings.append("Segment combination word count error")
        
        return len(warnings) == 0, warnings

    def detect_encoding_issues(self, file_path: Path) -> List[str]:
        """Detect potential encoding issues in files."""
        issues = []
        
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            # Check for common encoding issues
            if '\ufffd' in content:  # Replacement character
                issues.append("Contains Unicode replacement characters (encoding issue)")
            
            # Check for Romanian diacritics
            ro_chars = ['ă', 'â', 'î', 'ș', 'ț', 'Ă', 'Â', 'Î', 'Ș', 'Ț']
            has_diacritics = any(char in content for char in ro_chars)
            
            if file_path.name.endswith('_ro.md') and not has_diacritics and len(content) > 100:
                issues.append("Romanian file missing diacritics (possible encoding issue)")
            
        except UnicodeDecodeError:
            issues.append("Cannot decode file as UTF-8")
        except Exception as e:
            issues.append(f"Error reading file: {str(e)}")
        
        return issues

    def validate_translation_completeness(self, chapter_number: int) -> Dict:
        """Comprehensive validation of translation completeness."""
        result = {
            'valid': True,
            'errors': [],
            'warnings': [],
            'stats': {}
        }
        
        try:
            # Find all segment pairs
            en_pattern = f"*Chapter_{chapter_number}_seg*_of_*.md"
            ro_pattern = f"*Chapter_{chapter_number}_seg*_of_*_ro.md"
            
            en_segments = sorted(list(self.dirs['02_en_segments'].glob(en_pattern)))
            ro_segments = sorted(list(self.dirs['03_ro_segments'].glob(ro_pattern)))
            
            if not en_segments:
                result['errors'].append(f"No English segments found for chapter {chapter_number}")
                result['valid'] = False
                return result
            
            total_en_words = 0
            total_ro_words = 0
            translated_count = 0
            
            for en_file in en_segments:
                # Check English segment
                encoding_issues = self.detect_encoding_issues(en_file)
                if encoding_issues:
                    result['warnings'].extend([f"{en_file.name}: {issue}" for issue in encoding_issues])
                
                with open(en_file, 'r', encoding='utf-8') as f:
                    en_content = f.read()
                en_words = self.count_words(en_content)
                total_en_words += en_words
                
                # Find corresponding Romanian file
                ro_file = None
                for ro_f in ro_segments:
                    if self._segments_match(en_file.name, ro_f.name):
                        ro_file = ro_f
                        break
                
                if not ro_file:
                    result['errors'].append(f"Missing Romanian file for {en_file.name}")
                    result['valid'] = False
                    continue
                
                # Check Romanian segment
                encoding_issues = self.detect_encoding_issues(ro_file)
                if encoding_issues:
                    result['warnings'].extend([f"{ro_file.name}: {issue}" for issue in encoding_issues])
                
                with open(ro_file, 'r', encoding='utf-8') as f:
                    ro_content = f.read()
                
                # Remove metadata
                ro_lines = ro_content.split('\n')
                if ro_lines[0].strip().startswith('<!--'):
                    for j, line in enumerate(ro_lines):
                        if '-->' in line:
                            ro_content = '\n'.join(ro_lines[j+1:]).strip()
                            break
                
                ro_words = self.count_words(ro_content)
                
                if ro_words > 0:
                    translated_count += 1
                    total_ro_words += ro_words
                    
                    # Check translation quality indicators
                    ratio = ro_words / en_words if en_words > 0 else 0
                    is_final = self._is_final_segment(en_file)
                    
                    if ratio < RATIO_ERROR_THRESHOLD:
                        result['errors'].append(f"{ro_file.name}: Translation too short (ratio: {ratio:.2f})")
                        result['valid'] = False
                    elif ratio < RATIO_WARNING_LOW and not is_final:
                        result['warnings'].append(f"{ro_file.name}: Translation shorter than expected (ratio: {ratio:.2f})")
                    elif ratio > RATIO_WARNING_HIGH * 2:  # Very long
                        result['warnings'].append(f"{ro_file.name}: Translation much longer than expected (ratio: {ratio:.2f})")
                    
                    # Check for incomplete translations
                    if ro_content.endswith('...') or '[TODO]' in ro_content.upper():
                        result['warnings'].append(f"{ro_file.name}: Translation appears incomplete")
                else:
                    result['warnings'].append(f"{ro_file.name}: Not translated")
            
            result['stats'] = {
                'total_segments': len(en_segments),
                'translated_segments': translated_count,
                'completion_percentage': (translated_count / len(en_segments)) * 100 if en_segments else 0,
                'total_en_words': total_en_words,
                'total_ro_words': total_ro_words,
                'overall_ratio': total_ro_words / total_en_words if total_en_words > 0 else 0
            }
            
        except Exception as e:
            result['errors'].append(f"Validation error: {str(e)}")
            result['valid'] = False
        
        return result

    def load_config(self) -> Dict:
        """Load project configuration."""
        if self.config_file.exists():
            with open(self.config_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        return {}

    def save_config(self, config: Dict):
        """Save project configuration."""
        with open(self.config_file, 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=2, ensure_ascii=False)

    def load_log(self) -> Dict:
        """Load translation log."""
        if self.log_file.exists():
            with open(self.log_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        return {
            "project": {},
            "chapters": {},
            "warnings": []
        }

    def save_log(self, log: Dict):
        """Save translation log."""
        self.log_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.log_file, 'w', encoding='utf-8') as f:
            json.dump(log, f, indent=2, ensure_ascii=False)

    def init_project(self, epub_file: str = None):
        """Initialize new translation project."""
        console.print("[blue]Initializing translation project...[/blue]")
        
        # Create all directory structure
        for dir_name, dir_path in self.dirs.items():
            dir_path.mkdir(parents=True, exist_ok=True)
            console.print(f"OK Created directory: {dir_name}")
        
        # Create statistics subdirectory
        (self.dirs['06_tracking'] / 'statistics').mkdir(exist_ok=True)
        
        if epub_file:
            epub_path = Path(epub_file)
            if not epub_path.exists():
                raise FileNotFoundError(f"EPUB file not found: {epub_file}")
            
            if epub_path.suffix.lower() != '.epub':
                raise ValueError(f"File must be .epub format: {epub_file}")
            
            # Copy EPUB to project
            dest_path = self.dirs['00_en_full_epub'] / epub_path.name
            shutil.copy2(epub_path, dest_path)
            console.print(f"OK Copied EPUB: {epub_path.name}")
            
            # Extract book name from filename
            book_name = epub_path.stem
            
            # Create initial configuration
            config = {
                "book_name": book_name,
                "epub_file": epub_path.name,
                "source_language": "en",
                "target_language": "ro",
                "created": datetime.now().isoformat(),
                "project_dir": str(self.project_dir)
            }
            self.save_config(config)
            
            # Create initial log
            log = {
                "project": {
                    "book_name": book_name,
                    "created": datetime.now().isoformat(),
                    "total_chapters": 0,
                    "total_words_en": 0
                },
                "chapters": {},
                "warnings": []
            }
            self.save_log(log)
            
            console.print(f"OK Project initialized for: [green]{book_name}[/green]")
            console.print(f"OK Configuration saved")
            console.print(f"OK Translation log created")
            
            # Automatically extract chapters
            console.print("\n[blue]Automatically extracting chapters...[/blue]")
            self.extract_chapters()
            
            # Automatically split all chapters into segments
            console.print("\n[blue]Automatically splitting chapters into segments...[/blue]")
            self.split_all_chapters()
            
        else:
            console.print("OK Project structure created")
            console.print("[yellow]Note: No EPUB file specified. Use --init [epub_file] to add book.[/yellow]")
        
        console.print("\n[green]Project initialization complete![/green]")
        if epub_file:
            console.print("OK Chapters extracted and split into segments")
            console.print("Next step: Use --open-chapter [number] to start translation work")
        else:
            console.print("Next step: Use --init [epub_file] to add book and extract chapters")

    def extract_chapters(self):
        """Extract chapters from EPUB using spine as primary guide, TOC as supplementary."""
        console.print("[blue]Extracting chapters from EPUB using spine with TOC enhancement...[/blue]")
        
        config = self.load_config()
        if not config or 'epub_file' not in config:
            raise ValueError("No EPUB file configured. Run --init first.")
        
        epub_path = self.dirs['00_en_full_epub'] / config['epub_file']
        if not epub_path.exists():
            raise FileNotFoundError(f"EPUB file not found: {epub_path}")
        
        # Load EPUB
        book = epub.read_epub(str(epub_path))
        
        # Get book name for chapter files
        book_name = config['book_name']
        
        # Build TOC mapping for supplementary information
        toc_mapping = self._build_toc_mapping(book)
        
        sequential_count = 0
        total_words = 0
        log = self.load_log()
        
        # Clear existing chapters
        for existing_file in self.dirs['01_en_chapters'].glob('*.md'):
            existing_file.unlink()
        
        # Get spine items (reading order)
        spine_items = []
        for spine_id, linear in book.spine:
            item = book.get_item_with_id(spine_id)
            if item and item.get_type() == ebooklib.ITEM_DOCUMENT:
                spine_items.append(item)
        
        console.print(f"Found {len(spine_items)} spine items to process")
        console.print(f"TOC contains {len(toc_mapping)} mapped entries")
        
        # Process spine items in order
        for item in spine_items:
            # Parse HTML content
            soup = BeautifulSoup(item.get_content(), 'html.parser')
            
            # Extract text content preserving paragraph structure
            text = self._extract_text_with_paragraphs(soup)
            
            # Clean up text
            text = re.sub(r'\n\s*\n\s*\n+', '\n\n', text)
            text = text.strip()
            
            # Skip very short content (likely navigation/metadata)
            if len(text) < 50:
                continue
            
            # Get original filename for TOC lookup
            original_name = item.get_name()
            
            # Look up TOC information (title, chapter type)
            toc_info = toc_mapping.get(original_name, {})
            if not toc_info:
                filename_only = original_name.split('/')[-1] if '/' in original_name else original_name
                toc_info = toc_mapping.get(filename_only, {})
            
            toc_title = toc_info.get('title', '')
            
            # Determine if this should be processed as content
            is_likely_content = self._is_likely_main_content(text, original_name, toc_title)
            
            if is_likely_content:
                sequential_count += 1
                
                # Determine chapter type
                if toc_title:
                    chapter_type = self._categorize_chapter_type(toc_title)
                    title = f"# {toc_title}"
                    descriptive_name = self._create_descriptive_filename(toc_title, chapter_type)
                else:
                    # Generate title from content or filename
                    title = self._extract_chapter_title(text, sequential_count)
                    chapter_type = self._infer_chapter_type_from_content(text, original_name)
                    descriptive_name = self._create_descriptive_filename(title.replace('# ', ''), chapter_type)
                
                filename = f"{sequential_count:02d}_{descriptive_name}.md"
                
                # Count words
                word_count = self.count_words(text)
                total_words += word_count
                
                # Save file
                chapter_path = self.dirs['01_en_chapters'] / filename
                with open(chapter_path, 'w', encoding='utf-8') as f:
                    f.write(f"{title}\n\n")
                    f.write(text)
                
                # Update log
                log['chapters'][str(sequential_count)] = {
                    "title": title.replace('# ', ''),
                    "filename": filename,
                    "word_count": word_count,
                    "extracted": datetime.now().isoformat(),
                    "status": "extracted",
                    "chapter_type": chapter_type,
                    "toc_title": toc_title,
                    "original_file": original_name
                }
                
                source = "TOC" if toc_title else "content"
                console.print(f"[green]OK[/green] {chapter_type.title()} {filename}: {title.replace('# ', '')} ({word_count} words) [{source}]")
        
        # Update project log
        log['project']['total_chapters'] = sequential_count
        log['project']['total_words_en'] = total_words
        log['project']['last_extraction'] = datetime.now().isoformat()
        self.save_log(log)
        
        console.print(f"\n[green]Extraction complete![/green]")
        console.print(f"OK Extracted {sequential_count} chapters from spine")
        console.print(f"OK Total words: {total_words:,}")
        console.print(f"OK Average words per chapter: {total_words // sequential_count if sequential_count else 0:,}")
        console.print("\nNext step: Use --split-chapter [number] or --split-all-chapters")

    def _is_real_chapter(self, filename: str, content: str) -> bool:
        """Determine if this is a real chapter or metadata for MS6/Bands of Mourning."""
        content_lower = content.lower()
        lines = content.split('\n')
        first_line = lines[0].strip() if lines else ''
        
        # Strong metadata indicators - immediately exclude
        metadata_patterns = [
            'copyright', 'acknowledgments', 'about the author', 'tom doherty associates',
            'newsletter', 'sign up for', 'for email updates', 'dedication',
            'contents', 'title page', 'ars arcanum', 'postscript', 'by brandon sanderson',
            'for ben olsen', 'begin reading', 'this is a work of fiction'
        ]
        
        for pattern in metadata_patterns:
            if pattern in content_lower:
                return False
        
        word_count = self.count_words(content)
        
        # Check if it's a numbered chapter (1, 2, 3, etc.) at the start
        if first_line.isdigit() and int(first_line) <= 50:
            return True
            
        # Check for story chapters
        if first_line.lower() in ['prologue', 'epilogue']:
            return True
            
        # Check for part separators (these are very short)
        if first_line.lower().startswith('part ') and word_count < 10:
            return True
        
        # Strong story indicators - character names and dialogue
        story_indicators = [
            'waxillium', 'wax said', 'wayne said', 'marasi', 'steris', 
            'telsin', '"', 'he said', 'she said', 'wax ', 'wayne ', 'marasi '
        ]
        
        story_score = sum(1 for indicator in story_indicators if indicator in content_lower)
        
        # If has story content (substantial word count + story elements), it's likely a chapter
        if word_count > 1500 and story_score >= 2:
            return True
            
        # Special case: very long content with dialogue is likely a chapter
        if word_count > 2000 and '"' in content:
            return True
        
        # If very short with no story elements, likely metadata
        if word_count < 500:
            return False
            
        # Default to False for ambiguous cases (be conservative)
        return False

    def _extract_chapter_title(self, content: str, chapter_num: int) -> str:
        """Extract real chapter title from content."""
        lines = content.split('\n')
        
        # Look for chapter title patterns
        for line in lines[:10]:  # Check first 10 lines
            line = line.strip()
            if not line:
                continue
                
            # Pattern: "CHAPTER One", "Chapter 1", etc.
            chapter_match = re.match(r'^CHAPTER\s+(.+)$', line, re.IGNORECASE)
            if chapter_match:
                chapter_title = chapter_match.group(1).strip()
                # Convert "One" to proper case
                return f"# Chapter {chapter_title.title()}"
            
            # Pattern: "1", "One", etc. (standalone)
            if re.match(r'^(One|Two|Three|Four|Five|Six|Seven|Eight|Nine|Ten|\d+)$', line, re.IGNORECASE):
                return f"# Chapter {line.title()}"
        
        # Fallback
        return f"# Chapter {chapter_num}"

    def _extract_metadata_name(self, filename: str) -> str:
        """Extract clean name from metadata filename."""
        # Remove common prefixes and suffixes
        name = filename
        
        # Remove file extension
        if '.' in name:
            name = name.split('.')[0]
        
        # Remove ISBN/ID patterns more aggressively
        name = re.sub(r'text\d+_?', '', name)  # Remove text followed by numbers
        name = re.sub(r'^\d+_?', '', name)    # Remove leading numbers
        name = re.sub(r'_\d+$', '', name)     # Remove trailing numbers
        
        # Clean up underscores and special chars
        name = re.sub(r'[^\w\s]', '', name)
        name = re.sub(r'_+', '_', name)       # Multiple underscores to single
        name = re.sub(r'^_|_$', '', name)     # Remove leading/trailing underscores
        name = re.sub(r'\s+', '_', name.strip())
        
        # Common replacements
        replacements = {
            'aboutpublisher': 'aboutpublisher',
            'backmatterpage': 'backmatter',
            'frontmatterpage': 'frontmatter',
            'adcard': 'adcard',
            'contents': 'contents',
            'copyright': 'copyright',
            'dedication': 'dedication',
            'title': 'title'
        }
        
        for old, new in replacements.items():
            if old in name.lower():
                return new
        
        return name or 'metadata'

    def _extract_metadata_title(self, content: str, clean_name: str) -> str:
        """Extract title from metadata content."""
        lines = content.split('\n')
        
        # Look for the first substantial line as title
        for line in lines[:5]:
            line = line.strip()
            if len(line) > 3 and not line.startswith('#'):
                return f"# {line}"
        
        # Fallback to clean name
        return f"# {clean_name.replace('_', ' ').title()}"

    def _extract_text_with_paragraphs(self, soup: BeautifulSoup) -> str:
        """Extract text from HTML while preserving paragraph structure."""
        # Remove script and style elements
        for script in soup(["script", "style"]):
            script.decompose()
        
        text_parts = []
        
        # Process <p> tags specifically for paragraph content
        paragraphs = soup.find_all('p')
        
        if paragraphs:
            # Extract text from each paragraph tag
            for p in paragraphs:
                paragraph_text = p.get_text().strip()
                if paragraph_text and len(paragraph_text) > 3:  # Skip empty or very short content
                    text_parts.append(paragraph_text)
        
        # If no paragraphs found, try other block elements
        if not text_parts:
            for element in soup.find_all(['div', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6']):
                element_text = element.get_text().strip()
                if element_text and len(element_text) > 3:
                    text_parts.append(element_text)
        
        # If still no content found, fall back to basic text extraction
        if not text_parts:
            return soup.get_text()
        
        # Join paragraphs with double newlines to preserve EPUB formatting
        return '\n\n'.join(text_parts)

    def _build_toc_mapping(self, book):
        """Build a mapping from file paths to TOC information using contents.xhtml."""
        toc_mapping = {}
        
        # Try to get TOC from contents.xhtml first
        contents_item = book.get_item_with_id('contents')
        if contents_item:
            from bs4 import BeautifulSoup
            content = contents_item.get_content()
            soup = BeautifulSoup(content, 'html.parser')
            
            # Find all links in the contents
            links = soup.find_all('a')
            for link in links:
                href = link.get('href', '')
                text = link.get_text().strip()
                
                if href and text:
                    # Clean href to get just the filename
                    href_file = href.split('#')[0] if '#' in href else href
                    
                    # Determine if this is a story chapter based on title
                    is_story_chapter = self._is_story_chapter_from_toc_title(text)
                    
                    # Check if we already have this file in mapping
                    if href_file in toc_mapping:
                        # Priority: Prefer numbered chapters over broadsheet/part entries
                        existing_title = toc_mapping[href_file]['title'].lower()
                        new_title = text.lower()
                        
                        # Prefer "chapter X" over "part X" or broadsheet entries
                        if ('chapter' in new_title and any(c.isdigit() for c in new_title)) and \
                           (('part' in existing_title and any(c.isdigit() for c in existing_title)) or 
                            'broadsheet' in existing_title):
                            # Replace with the chapter entry
                            toc_mapping[href_file] = {
                                'title': text,
                                'is_chapter': is_story_chapter,
                                'level': 0
                            }
                        # Don't replace if existing entry is better
                    else:
                        # New entry
                        toc_mapping[href_file] = {
                            'title': text,
                            'is_chapter': is_story_chapter,
                            'level': 0
                        }
        
        # Fallback: Try ebooklib TOC if contents.xhtml didn't work
        if not toc_mapping:
            def process_toc_item(item, level=0):
                """Recursively process TOC items."""
                if hasattr(item, 'title') and hasattr(item, 'href'):
                    # Clean href to get just the filename
                    href_file = item.href.split('#')[0] if '#' in item.href else item.href
                    
                    # Determine if this is a story chapter based on title
                    title = item.title.strip()
                    is_story_chapter = self._is_story_chapter_from_toc_title(title)
                    
                    toc_mapping[href_file] = {
                        'title': title,
                        'is_chapter': is_story_chapter,
                        'level': level
                    }
                    
                elif isinstance(item, tuple) and len(item) == 2:
                    # Handle nested TOC structure (section, children)
                    section, children = item
                    if hasattr(section, 'title'):
                        # Process section header if it has an href
                        if hasattr(section, 'href'):
                            href_file = section.href.split('#')[0] if '#' in section.href else section.href
                            title = section.title.strip()
                            is_story_chapter = self._is_story_chapter_from_toc_title(title)
                            
                            toc_mapping[href_file] = {
                                'title': title,
                                'is_chapter': is_story_chapter,
                                'level': level
                            }
                    
                    # Process children
                    for child in children:
                        process_toc_item(child, level + 1)
                        
                elif isinstance(item, list):
                    # Handle list of items
                    for child in item:
                        process_toc_item(child, level)
            
            # Process all TOC items
            for toc_item in book.toc:
                process_toc_item(toc_item)
        
        return toc_mapping

    def _is_story_chapter_from_toc_title(self, title: str) -> bool:
        """Determine if a TOC entry represents a story chapter (regular or special)."""
        chapter_type = self._categorize_chapter_type(title)
        return chapter_type in ['regular', 'special']

    def _categorize_chapter_type(self, title: str) -> str:
        """Categorize chapter type: 'special', 'regular', or 'metadata'."""
        title_lower = title.lower().strip()
        
        # Special chapters (non-numbered story chapters)
        special_patterns = [
            'prologue', 'epilogue', 'preface', 'introduction', 'intermezzo',
            'interlude', 'intermission', 'aside', 'appendix', 'ars arcanum'
        ]
        
        # Check for special chapters first
        for pattern in special_patterns:
            if pattern in title_lower:
                return 'special'
        
        # Check if it's a numbered chapter using regex - more flexible
        import re
        if re.match(r'^chapter\s+\d+$', title_lower):
            return 'regular'
        
        # Check if it contains "chapter" and a number (catch-all for numbered chapters)
        if 'chapter' in title_lower and any(char.isdigit() for char in title_lower):
            return 'regular'
        
        # Metadata patterns - these are NOT story chapters
        metadata_patterns = [
            'title page', 'copyright', 'dedication', 'acknowledgments',
            'map of', 'about the author', 'postscript',
            'newsletter', 'broadsheet', 'part one', 'part two', 'part three',
            'by brandon sanderson', 'contents', 'table of contents'
        ]
        
        # Check for metadata patterns
        for pattern in metadata_patterns:
            if pattern in title_lower:
                return 'metadata'
        
        # Default: if we can't categorize, assume it's a special chapter
        # This handles cases like unnamed chapters that are still story content
        return 'special'

    def _create_descriptive_filename(self, toc_title: str, chapter_type: str) -> str:
        """Create descriptive filename from TOC title."""
        if not toc_title:
            return "Unknown_Chapter"
        
        # Clean up the title for filename use
        clean_title = toc_title.strip()
        
        # Handle specific cases
        if chapter_type == 'special':
            if 'prologue' in clean_title.lower():
                return 'Prologue'
            elif 'epilogue' in clean_title.lower():
                # Handle numbered epilogues with specific names
                import re
                if re.search(r'epilogue\s+\d+', clean_title.lower()):
                    return 'Epilogue'  # Keep generic for numbered epilogues
                else:
                    # For named epilogues like "EPILOGUE 1", "MARASI", etc.
                    # Just use the title as-is, cleaned up
                    return clean_title.replace(' ', '_').replace(':', '_')
            elif 'interlude' in clean_title.lower():
                return clean_title.replace(':', '_').replace(' ', '_')
            elif 'intermezzo' in clean_title.lower():
                return clean_title.replace(':', '_').replace(' ', '_')
            elif 'ars arcanum' in clean_title.lower():
                return 'Ars_Arcanum'
            else:
                # Generic special chapter
                return clean_title.replace(':', '_').replace(' ', '_')
        
        elif chapter_type == 'regular':
            # For numbered chapters, extract the number and create simple name
            if 'chapter' in clean_title.lower():
                # Extract number or word number
                import re
                number_match = re.search(r'chapter\s+(\w+)', clean_title.lower())
                if number_match:
                    chapter_num = number_match.group(1)
                    return f"Chapter_{chapter_num.title()}"
            return clean_title.replace(' ', '_')
        
        else:  # metadata
            # For metadata, create descriptive name
            clean = clean_title.lower()
            if 'copyright' in clean:
                return 'Copyright'
            elif 'dedication' in clean:
                return 'Dedication'
            elif 'acknowledgment' in clean:
                return 'Acknowledgments'
            elif 'about' in clean and 'author' in clean:
                return 'About_Author'
            elif 'contents' in clean or 'table' in clean:
                return 'Contents'
            else:
                return clean_title.replace(' ', '_').replace(':', '').replace('-', '_')

    def _is_likely_main_content(self, text: str, filename: str, toc_title: str) -> bool:
        """Determine if this content is likely main story content."""
        # If it's in TOC and marked as chapter, it's definitely content
        if toc_title and self._is_story_chapter_from_toc_title(toc_title):
            return True
            
        # Skip obvious navigation/metadata files
        filename_lower = filename.lower()
        skip_patterns = ['nav', 'toc', 'cover', 'title', 'copyright', 'contents', 'index']
        if any(pattern in filename_lower for pattern in skip_patterns):
            return False
            
        # Content heuristics - look for substantial narrative text
        lines = text.split('\n')
        non_empty_lines = [line.strip() for line in lines if line.strip()]
        
        # Must have reasonable length
        if len(non_empty_lines) < 10:
            return False
            
        # Look for story-like content patterns
        word_count = len(text.split())
        if word_count < 200:  # Very short content
            return False
            
        # Look for chapter indicators in content
        text_lower = text.lower()
        chapter_indicators = ['chapter', 'prologue', 'epilogue', 'interlude', 'part']
        has_chapter_indicator = any(indicator in text_lower for indicator in chapter_indicators)
        
        # Look for narrative patterns (dialogue, action, etc.)
        narrative_patterns = ['"', 'said', 'looked', 'walked', 'felt', 'thought', 'knew']
        narrative_score = sum(1 for pattern in narrative_patterns if pattern in text_lower)
        
        # Combine factors
        is_likely = (
            word_count >= 500 or  # Decent length
            narrative_score >= 3 or  # Narrative elements
            has_chapter_indicator  # Explicit chapter content
        )
        
        return is_likely

    def _infer_chapter_type_from_content(self, text: str, filename: str) -> str:
        """Infer chapter type from content when TOC info is missing."""
        text_lower = text.lower()
        
        # Check for special types
        if 'prologue' in text_lower:
            return 'special'
        elif 'epilogue' in text_lower:
            return 'special'
        elif 'interlude' in text_lower:
            return 'special'
        elif 'intermezzo' in text_lower:
            return 'special'
        elif 'ars arcanum' in text_lower:
            return 'special'
        elif any(word in text_lower for word in ['dedication', 'acknowledgment', 'about the author']):
            return 'metadata'
        else:
            # Default to regular chapter
            return 'regular'

    def split_chapter(self, chapter_identifier):
        """Split chapter into segments using critical algorithm."""
        # Handle both int and string identifiers
        if isinstance(chapter_identifier, int):
            chapter_key = str(chapter_identifier)
            display_name = f"Chapter {chapter_identifier}"
        else:
            chapter_key = chapter_identifier
            if chapter_key.startswith('meta_'):
                display_name = f"Metadata {chapter_key}"
            else:
                display_name = f"Chapter {chapter_key}"
        
        console.print(f"[blue]Splitting {display_name} into segments...[/blue]")
        
        log = self.load_log()
        
        # Find chapter file
        if chapter_key not in log['chapters']:
            raise ValueError(f"{display_name} not found. Extract chapters first.")
        
        chapter_info = log['chapters'][chapter_key]
        chapter_file = self.dirs['01_en_chapters'] / chapter_info['filename']
        
        if not chapter_file.exists():
            console.print(f"[yellow]WARNING  Skipping {display_name}: file not found ({chapter_info['filename']})[/yellow]")
            return
        
        # Read chapter content
        with open(chapter_file, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # Remove the header line we added
        lines = content.split('\n')
        if lines[0].startswith('# '):
            content = '\n'.join(lines[2:])  # Skip title and empty line
        
        # CRITICAL SEGMENTATION ALGORITHM
        segments = self._split_content_into_segments(content, chapter_info['title'])
        
        # Create file prefix for segments
        if chapter_key.startswith('meta_'):
            # For metadata: use original filename without extension as prefix
            base_filename = chapter_info['filename'].replace('.md', '')
            file_prefix = base_filename
        else:
            # For chapters: use traditional format - replace spaces with underscores
            clean_title = chapter_info['title'].replace(' ', '_')
            file_prefix = f"{int(chapter_key):02d}_{clean_title}"
        
        # Clear existing segments for this specific chapter/metadata
        pattern = f"{file_prefix}_*"
        
        # Always clear English segments for this chapter
        for existing_file in self.dirs['02_en_segments'].glob(pattern):
            existing_file.unlink()
        
        # Smart logic for Romanian segments - check if any translations exist for this chapter
        has_chapter_translations = False
        for existing_file in self.dirs['03_ro_segments'].glob(pattern):
            try:
                with open(existing_file, 'r', encoding='utf-8') as f:
                    content = f.read().strip()
                    if content and not content.startswith('<!--'):  # Non-empty and not just metadata
                        has_chapter_translations = True
                        break
            except:
                pass
        
        if has_chapter_translations:
            console.print(f"WARNING  Found existing translations for {display_name} - preserving them")
        else:
            # Only clear Romanian segments if none have translations
            for existing_file in self.dirs['03_ro_segments'].glob(pattern):
                existing_file.unlink()
        
        total_segments = len(segments)
        total_words = 0
        
        # Save segments and create Romanian placeholders
        for i, segment_content in enumerate(segments):
            segment_num = i + 1
            is_final = (segment_num == total_segments)
            
            # English segment filename
            en_filename = f"{file_prefix}_seg{segment_num:02d}_of_{total_segments:02d}.md"
            
            # Romanian segment filename
            ro_filename = f"{file_prefix}_seg{segment_num:02d}_of_{total_segments:02d}_ro.md"
            
            # Count words in segment
            segment_words = self.count_words(segment_content)
            total_words += segment_words
            
            # Save English segment
            en_path = self.dirs['02_en_segments'] / en_filename
            with open(en_path, 'w', encoding='utf-8') as f:
                f.write(segment_content)
            
            # Create Romanian placeholder (empty file) only if it doesn't exist or is empty
            ro_path = self.dirs['03_ro_segments'] / ro_filename
            should_create_ro = True
            
            if ro_path.exists():
                try:
                    with open(ro_path, 'r', encoding='utf-8') as f:
                        existing_content = f.read().strip()
                        if existing_content and not existing_content.startswith('<!--'):
                            should_create_ro = False  # File has translation content, don't overwrite
                except:
                    pass
            
            if should_create_ro:
                with open(ro_path, 'w', encoding='utf-8') as f:
                    f.write("")  # Completely empty file ready for translation
            
            status_icon = "FINAL" if is_final else "PAGE"
            console.print(f"OK {status_icon} Segment {segment_num}/{total_segments}: {segment_words} words")
        
        # Update log
        log['chapters'][chapter_key]['segments'] = total_segments
        log['chapters'][chapter_key]['status'] = 'segmented'
        log['chapters'][chapter_key]['segmented'] = datetime.now().isoformat()
        log['chapters'][chapter_key]['segment_words'] = total_words
        
        # Verify word count preservation - recalculate original without title for accurate comparison
        with open(chapter_file, 'r', encoding='utf-8') as f:
            original_content = f.read()
        
        # Remove title for fair comparison (same as what we segmented)
        lines = original_content.split('\n')
        if lines[0].startswith('# '):
            original_content = '\n'.join(lines[2:])  # Skip title and empty line
        
        original_words_actual = self.count_words(original_content)
        
        if abs(total_words - original_words_actual) > 5:  # Allow small variance
            warning = f"{display_name}: Word count mismatch (original: {original_words_actual}, segments: {total_words})"
            # Avoid duplicate warnings
            if warning not in log['warnings']:
                log['warnings'].append(warning)
            console.print(f"[yellow]WARNING  {warning}[/yellow]")
        
        self.save_log(log)
        
        console.print(f"\n[green]{display_name} split complete![/green]")
        console.print(f"OK Created {total_segments} segments")
        console.print(f"OK Total words: {total_words} (original: {original_words_actual})")
        console.print(f"OK Romanian placeholders created")
        
        if total_segments > 1:
            final_segment_words = self.count_words(segments[-1])
            if final_segment_words < 50:
                console.print(f"[cyan]INFO  Final segment has only {final_segment_words} words - this is normal[/cyan]")

    def _split_content_into_segments(self, content: str, chapter_title: str) -> List[str]:
        """
        CRITICAL segmentation algorithm that NEVER loses content.
        Follows the exact pseudocode from the specification.
        """
        # For small content (metadata), don't segment at all
        total_words = self.count_words(content)
        if total_words < 200:  # Small metadata, keep as single segment
            return [content.strip()]
        
        segments = []
        current_segment = []
        current_word_count = 0
        
        # Split by double newlines (paragraphs)
        paragraphs = re.split(r'\n\s*\n', content.strip())
        total_paragraphs = len(paragraphs)
        
        for index, paragraph in enumerate(paragraphs):
            paragraph = paragraph.strip()
            if not paragraph:  # Skip empty paragraphs
                continue
                
            word_count = self.count_words(paragraph)
            is_last_paragraph = (index == total_paragraphs - 1)
            
            if current_word_count + word_count <= MAX_WORDS_PER_SEGMENT:
                # Fits within limit
                current_segment.append(paragraph)
                current_word_count += word_count
            else:
                # Would exceed limit
                if current_word_count >= MIN_WORDS_INTERMEDIATE:
                    # Current segment is big enough, save it and start new
                    if current_segment:
                        segments.append('\n\n'.join(current_segment))
                    current_segment = [paragraph]
                    current_word_count = word_count
                else:
                    # Current segment too small for intermediate, add paragraph anyway
                    current_segment.append(paragraph)
                    current_word_count += word_count
            
            # Save final segment when we reach the last paragraph
            if is_last_paragraph and current_segment:
                segments.append('\n\n'.join(current_segment))
                current_segment = []  # Clear after saving
        
        # Ensure we have at least one segment
        if not segments and content.strip():
            segments.append(content.strip())
        
        return segments

    def _sort_chapter_keys(self, keys):
        """Sort chapter keys, putting numeric chapters first, then metadata."""
        numeric_keys = []
        meta_keys = []
        
        for key in keys:
            if key.startswith('meta_'):
                meta_keys.append(key)
            else:
                try:
                    int(key)
                    numeric_keys.append(key)
                except ValueError:
                    # Legacy or other keys, skip
                    continue
        
        # Sort numeric keys by integer value, meta keys alphabetically
        numeric_keys.sort(key=int)
        meta_keys.sort()
        
        return numeric_keys + meta_keys

    def _is_final_segment(self, filename):
        """Check if a segment is the final one by parsing filename format: XX_Title_segYY_of_ZZ.md"""
        try:
            name_parts = filename.stem.split('_seg')
            if len(name_parts) == 2:
                seg_info = name_parts[1]  # "01_of_04" or similar
                if '_of_' in seg_info:
                    current, total = seg_info.split('_of_')
                    return current == total
        except:
            pass
        return False

    def split_all_chapters(self):
        """Split all chapters and metadata into segments."""
        console.print("[blue]Splitting all chapters and metadata into segments...[/blue]")
        
        # Scan 01_en_chapters folder directly to get actual files
        chapter_files = list(self.dirs['01_en_chapters'].glob('*.md'))
        if not chapter_files:
            raise ValueError("No chapter files found in 01_en_chapters/. Extract chapters first.")
        
        # Clear existing segments before starting
        console.print("[yellow]Clearing existing segments...[/yellow]")
        
        # Always clear English segments
        for existing_file in self.dirs['02_en_segments'].glob('*.md'):
            existing_file.unlink()
        
        # Smart logic for Romanian segments - check if any translations exist
        has_translations = False
        for ro_file in self.dirs['03_ro_segments'].glob('*.md'):
            try:
                with open(ro_file, 'r', encoding='utf-8') as f:
                    content = f.read().strip()
                    if content and not content.startswith('<!--'):  # Non-empty and not just metadata
                        has_translations = True
                        break
            except:
                pass
        
        if has_translations:
            console.print("WARNING  Found existing Romanian translations - preserving them")
        else:
            console.print("No Romanian translations found - clearing Romanian segments")
            for existing_file in self.dirs['03_ro_segments'].glob('*.md'):
                existing_file.unlink()
        
        console.print("OK Segment folders prepared")
        
        # Create mapping from physical files to identifiers
        file_identifiers = []
        for file_path in chapter_files:
            filename = file_path.name
            if filename.startswith('000'):
                # Metadata file - extract number to create meta_X identifier
                match = re.search(r'(\d+)', filename)
                if match:
                    meta_num = int(match.group(1))  # Use actual number from filename
                    file_identifiers.append(f'meta_{meta_num}')
            elif re.match(r'^\d+_', filename):
                # Chapter file - extract chapter number
                match = re.search(r'^(\d+)_', filename)
                if match:
                    chapter_num = int(match.group(1))
                    file_identifiers.append(str(chapter_num))
        
        # Sort identifiers (chapters first, then metadata)
        sorted_identifiers = self._sort_chapter_keys(file_identifiers)
        
        for identifier in sorted_identifiers:
            if identifier.startswith('meta_'):
                console.print(f"\n[cyan]Processing Metadata {identifier}[/cyan]")
            else:
                console.print(f"\n[cyan]Processing Chapter {identifier}[/cyan]")
            
            try:
                self.split_chapter(identifier)
            except ValueError as e:
                console.print(f"WARNING  Skipping {identifier}: {str(e)}")
                continue
        
        console.print(f"\n[green]All content split into segments![/green]")

    def prepare_manual_translation(self, chapter_number: int):
        """Prepare files for manual translation."""
        console.print(f"[blue]Preparing Chapter {chapter_number} for manual translation...[/blue]")
        
        log = self.load_log()
        chapter_key = str(chapter_number)
        
        if chapter_key not in log['chapters']:
            raise ValueError(f"Chapter {chapter_number} not found.")
        
        chapter_info = log['chapters'][chapter_key]
        
        if 'segments' not in chapter_info:
            raise ValueError(f"Chapter {chapter_number} not segmented. Run --split-chapter first.")
        
        # Find all English segments for this chapter
        pattern = f"*Chapter_{chapter_number}_seg*_of_*.md"
        en_segments = sorted(list(self.dirs['02_en_segments'].glob(pattern)))
        
        if not en_segments:
            raise ValueError(f"No segments found for chapter {chapter_number}")
        
        # Generate preparation file
        prep_filename = f"ready_for_translation_ch{chapter_number:02d}.txt"
        prep_path = self.project_dir / prep_filename
        
        with open(prep_path, 'w', encoding='utf-8') as f:
            f.write(f"CHAPTER {chapter_number} TRANSLATION PREPARATION\n")
            f.write("=" * 50 + "\n\n")
            f.write(f"Chapter Title: {chapter_info['title'].replace('_', ' ').title()}\n")
            f.write(f"Total Segments: {chapter_info['segments']}\n")
            f.write(f"Total Words: {chapter_info.get('segment_words', 'Unknown')}\n\n")
            
            f.write("SEGMENTS TO TRANSLATE:\n")
            f.write("-" * 25 + "\n")
            
            total_words = 0
            for i, en_file in enumerate(en_segments):
                # Read segment to count words
                with open(en_file, 'r', encoding='utf-8') as seg_f:
                    content = seg_f.read()
                word_count = self.count_words(content)
                total_words += word_count
                
                is_final = self._is_final_segment(en_file)
                final_marker = " (FINAL SEGMENT)" if is_final else ""
                
                f.write(f"{i+1:2d}. {en_file.name} - {word_count} words{final_marker}\n")
            
            f.write(f"\nTotal words to translate: {total_words}\n\n")
            
            f.write("TRANSLATION INSTRUCTIONS:\n")
            f.write("-" * 25 + "\n")
            f.write("1. Translate each segment from the 02_en_segments/ folder\n")
            f.write("2. Save translations in the corresponding 03_ro_segments/ files\n")
            f.write("3. Preserve ALL content - do not skip or summarize anything\n")
            f.write("4. Maintain paragraph breaks (double newlines)\n")
            f.write("5. Keep proper names unchanged\n")
            f.write("6. Final segments can be very small - this is normal\n\n")
            
            f.write("TRANSLATION PROMPT TEMPLATE:\n")
            f.write("-" * 30 + "\n")
            f.write("=== TRANSLATION INSTRUCTIONS ===\n")
            f.write("Translate the following English text to Romanian.\n\n")
            f.write("CRITICAL RULES:\n")
            f.write("1. Translate ALL content - do not skip or summarize anything\n")
            f.write("2. Preserve ALL paragraph breaks (empty lines between paragraphs)\n")
            f.write("3. Keep all proper names unchanged (John stays John, not Ion)\n")
            f.write("4. Maintain the original tone and style\n")
            f.write("5. Preserve any special formatting (*asterisks* or _underscores_)\n")
            f.write("6. Keep numbers, dates, and times in the same format\n")
            f.write("7. Translate idioms to natural Romanian equivalents\n\n")
            f.write("TEXT TO TRANSLATE:\n")
            f.write("[Content will be pasted here]\n\n")
            f.write("END OF TEXT\n")
            f.write("=== Please provide the complete Romanian translation below ===\n\n")
        
        console.print(f"OK Preparation file created: [green]{prep_filename}[/green]")
        
        # Display segment overview
        console.print(f"\n[cyan]Chapter {chapter_number} Segment Overview:[/cyan]")
        for i, en_file in enumerate(en_segments):
            with open(en_file, 'r', encoding='utf-8') as f:
                content = f.read()
            word_count = self.count_words(content)
            is_final = self._is_final_segment(en_file)
            icon = "FINAL" if is_final else "PAGE"
            console.print(f"{icon} Segment {i+1}: {word_count} words")
        
        console.print(f"\n[green]Chapter {chapter_number} ready for translation![/green]")
        console.print(f"OK Review {prep_filename} for detailed instructions")
        console.print(f"OK Translate files in 02_en_segments/ to 03_ro_segments/")
        console.print(f"OK Use --statistics {chapter_number} to check progress")

    def generate_statistics(self, chapter_number: int = None):
        """Generate translation statistics."""
        if chapter_number is None:
            self._generate_all_statistics()
        else:
            self._generate_chapter_statistics(chapter_number)

    def _generate_chapter_statistics(self, chapter_number: int):
        """Generate statistics for a specific chapter."""
        console.print(f"[blue]Generating statistics for Chapter {chapter_number}...[/blue]")
        
        log = self.load_log()
        chapter_key = str(chapter_number)
        
        if chapter_key not in log['chapters']:
            raise ValueError(f"Chapter {chapter_number} not found.")
        
        chapter_info = log['chapters'][chapter_key]
        
        if 'segments' not in chapter_info:
            raise ValueError(f"Chapter {chapter_number} not segmented. Run --split-chapter first.")
        
        # Find all segment pairs for this chapter
        en_pattern = f"*Chapter_{chapter_number}_seg*_of_*.md"
        ro_pattern = f"*Chapter_{chapter_number}_seg*_of_*_ro.md"
        
        en_segments = sorted(list(self.dirs['02_en_segments'].glob(en_pattern)))
        ro_segments = sorted(list(self.dirs['03_ro_segments'].glob(ro_pattern)))
        
        if not en_segments:
            raise ValueError(f"No English segments found for chapter {chapter_number}")
        
        # Create statistics data
        stats_data = []
        console_output = []
        word_verification_data = []
        total_en_words = 0
        total_ro_words = 0
        translated_count = 0
        
        for i, en_file in enumerate(en_segments):
            # Read English segment
            with open(en_file, 'r', encoding='utf-8') as f:
                en_content = f.read()
            
            en_words = self.count_words(en_content)
            en_chars = len(en_content)
            total_en_words += en_words
            
            # Find corresponding Romanian file
            ro_file = None
            for ro_f in ro_segments:
                if self._segments_match(en_file.name, ro_f.name):
                    ro_file = ro_f
                    break
            
            if ro_file and ro_file.exists():
                with open(ro_file, 'r', encoding='utf-8') as f:
                    ro_content = f.read()
                
                # Remove metadata header
                ro_lines = ro_content.split('\n')
                if ro_lines[0].strip().startswith('<!--'):
                    # Find end of comment
                    for j, line in enumerate(ro_lines):
                        if '-->' in line:
                            ro_content = '\n'.join(ro_lines[j+1:]).strip()
                            break
                
                ro_words = self.count_words(ro_content)
                ro_chars = len(ro_content)
                total_ro_words += ro_words
            else:
                ro_words = 0
                ro_chars = 0
                ro_content = ""
            
            # Determine status
            is_final = self._is_final_segment(en_file)
            ratio = ro_chars / en_chars if en_chars > 0 else 0
            
            if ro_words == 0:
                status = "NOT_TRANSLATED"
                status_icon = "✗"
                status_color = "red"
            elif ratio < RATIO_ERROR_THRESHOLD:
                status = "ERROR_INCOMPLETE"
                status_icon = "WARNING"
                status_color = "red"
            elif ratio < RATIO_WARNING_LOW and not is_final:
                status = "WARNING_SHORT"
                status_icon = "WARNING"
                status_color = "yellow"
            elif ratio > RATIO_WARNING_HIGH:
                status = "WARNING_LONG"
                status_icon = "WARNING"
                status_color = "yellow"
            elif is_final and ro_words < 50:
                status = "OK_SMALL_FINAL"
                status_icon = "OK"
                status_color = "cyan"
            else:
                status = "OK"
                status_icon = "OK"
                status_color = "green"
            
            if ro_words > 0:
                translated_count += 1
            
            # Add to statistics
            segment_name = en_file.stem
            stats_data.append({
                'Segment': segment_name,
                'EN_Words': en_words,
                'EN_Chars': en_chars,
                'RO_Words': ro_words,
                'RO_Chars': ro_chars,
                'Ratio': round(ratio, 2),
                'Is_Final': "Yes" if is_final else "No",
                'Status': status,
                'Notes': self._get_status_note(status, ro_words, is_final)
            })
            
            # Console output
            console_output.append((i+1, len(en_segments), en_words, ro_words, status_icon, status_color, is_final))
            
            # Word verification data (only for translated segments)
            if ro_words > 0:
                en_first_words = self._extract_first_words(en_content)
                en_last_words = self._extract_last_words(en_content)
                ro_first_words = self._extract_first_words(ro_content)
                ro_last_words = self._extract_last_words(ro_content)
                
                word_verification_data.append({
                    'segment_num': i + 1,
                    'segment_name': segment_name,
                    'en_first': en_first_words,
                    'ro_first': ro_first_words,
                    'en_last': en_last_words,
                    'ro_last': ro_last_words,
                    'status_color': status_color
                })
        
        # Generate CSV
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        csv_filename = f"Chapter_{chapter_number:02d}_Statistics_{timestamp}.csv"
        csv_path = self.dirs['06_tracking'] / 'statistics' / csv_filename
        csv_path.parent.mkdir(exist_ok=True)
        
        df = pd.DataFrame(stats_data)
        df.to_csv(csv_path, index=False)
        
        # Add summary to CSV
        with open(csv_path, 'a', encoding='utf-8') as f:
            f.write(f"\nSUMMARY\n")
            f.write(f"Total Segments,{len(en_segments)}\n")
            f.write(f"Translated,{translated_count}/{len(en_segments)} ({translated_count/len(en_segments)*100:.0f}%)\n")
            f.write(f"Total EN Words,{total_en_words}\n")
            f.write(f"Total RO Words,{total_ro_words}\n")
            f.write(f"Overall Ratio,{total_ro_words/total_en_words if total_en_words > 0 else 0:.2f}\n")
            
            final_segment = stats_data[-1] if stats_data else None
            if final_segment and final_segment['Is_Final'] == "Yes" and final_segment['RO_Words'] < 50:
                f.write(f"Note,Final segment contains only {final_segment['RO_Words']} words - verify this is correct\n")
        
        # Console output
        console.print(f"\n[cyan]Chapter {chapter_number} Translation Status[/cyan]")
        console.print("=" * 40)
        
        for seg_num, total_segs, en_words, ro_words, icon, color, is_final in console_output:
            final_marker = " - Final segment" if is_final else ""
            if ro_words > 0:
                console.print(f"[{color}]{icon} Segment {seg_num}/{total_segs}: OK ({en_words} words → {ro_words} words){final_marker}[/{color}]")
            else:
                console.print(f"[{color}]{icon} Segment {seg_num}/{total_segs}: NOT TRANSLATED{final_marker}[/{color}]")
        
        console.print("=" * 40)
        percentage = translated_count / len(en_segments) * 100
        console.print(f"Overall: {translated_count}/{len(en_segments)} segments translated ({percentage:.0f}%)")
        
        if translated_count < len(en_segments):
            missing = [str(i+1) for i, (_, _, _, ro_words, _, _, _) in enumerate(console_output) if ro_words == 0]
            console.print(f"[yellow]Action needed: Translate segment(s) {', '.join(missing)}[/yellow]")
        
        if stats_data and stats_data[-1]['Is_Final'] == "Yes" and stats_data[-1]['RO_Words'] < 50:
            console.print(f"[cyan]Note: Final segment is very small ({stats_data[-1]['RO_Words']} words) - this is normal[/cyan]")
        
        # Display word verification table if there are translated segments
        if word_verification_data:
            console.print(f"\n[cyan]Word Verification Table (Chapter {chapter_number})[/cyan]")
            
            table = Table(show_header=True, header_style="bold magenta")
            table.add_column("Seg", style="cyan", width=4)
            table.add_column("EN First 3 Words", style="blue", width=20)
            table.add_column("RO First 3 Words", style="green", width=20)
            table.add_column("EN Last 3 Words", style="blue", width=20)
            table.add_column("RO Last 3 Words", style="green", width=20)
            
            for data in word_verification_data:
                # Truncate long text to fit in columns
                en_first = data['en_first'][:20] + "..." if len(data['en_first']) > 20 else data['en_first']
                ro_first = data['ro_first'][:20] + "..." if len(data['ro_first']) > 20 else data['ro_first']
                en_last = data['en_last'][:20] + "..." if len(data['en_last']) > 20 else data['en_last']
                ro_last = data['ro_last'][:20] + "..." if len(data['ro_last']) > 20 else data['ro_last']
                
                # Use status color for the row
                style = data['status_color']
                table.add_row(
                    str(data['segment_num']),
                    en_first,
                    ro_first,
                    en_last,
                    ro_last,
                    style=style
                )
            
            console.print(table)
            console.print(f"[dim]This table shows first/last 3 words to verify complete translation coverage.[/dim]")
        
        console.print(f"\nOK Statistics saved: [green]{csv_filename}[/green]")

    def _generate_all_statistics(self):
        """Generate statistics for all chapters."""
        console.print("[blue]Generating statistics for all chapters...[/blue]")
        
        log = self.load_log()
        
        if not log['chapters']:
            raise ValueError("No chapters found. Extract chapters first.")
        
        for chapter_num in self._sort_chapter_keys(log['chapters'].keys()):
            console.print(f"\n[cyan]Chapter {chapter_num}[/cyan]")
            try:
                self._generate_chapter_statistics(int(chapter_num))
            except ValueError as e:
                console.print(f"[yellow]Skipped: {str(e)}[/yellow]")

    def _segments_match(self, en_filename: str, ro_filename: str) -> bool:
        """Check if English and Romanian segment files correspond."""
        # Remove .md extension and _ro suffix
        en_base = en_filename.replace('.md', '')
        ro_base = ro_filename.replace('_ro.md', '')
        return en_base == ro_base

    def _get_status_note(self, status: str, ro_words: int, is_final: bool) -> str:
        """Get explanatory note for status."""
        if status == "NOT_TRANSLATED":
            return "Missing translation"
        elif status == "ERROR_INCOMPLETE":
            return "Translation appears incomplete"
        elif status == "WARNING_SHORT":
            return "Translation shorter than expected"
        elif status == "WARNING_LONG":
            return "Translation longer than expected"
        elif status == "OK_SMALL_FINAL":
            return "Final segment - small size expected"
        else:
            return ""

    def _clean_content_for_word_extraction(self, content: str) -> str:
        """Clean content by removing metadata headers and extra whitespace."""
        if not content.strip():
            return ""
        
        lines = content.split('\n')
        
        # Remove metadata header if present
        if lines[0].strip().startswith('<!--'):
            for j, line in enumerate(lines):
                if '-->' in line:
                    content = '\n'.join(lines[j+1:]).strip()
                    break
        
        # Remove extra whitespace and normalize
        return ' '.join(content.split())

    def _extract_first_words(self, content: str, count: int = 3) -> str:
        """Extract first N words from content, ignoring metadata."""
        cleaned_content = self._clean_content_for_word_extraction(content)
        if not cleaned_content:
            return "N/A"
        
        words = cleaned_content.split()
        first_words = words[:count] if len(words) >= count else words
        return ' '.join(first_words)

    def _extract_last_words(self, content: str, count: int = 3) -> str:
        """Extract last N words from content."""
        cleaned_content = self._clean_content_for_word_extraction(content)
        if not cleaned_content:
            return "N/A"
        
        words = cleaned_content.split()
        last_words = words[-count:] if len(words) >= count else words
        return ' '.join(last_words)

    def combine_chapter(self, chapter_number: int):
        """Combine translated segments into complete chapter."""
        console.print(f"[blue]Combining Chapter {chapter_number} segments...[/blue]")
        
        # Find all Romanian segments for this chapter
        ro_pattern = f"*Chapter_{chapter_number}_seg*_of_*_ro.md"
        ro_segments = sorted(list(self.dirs['03_ro_segments'].glob(ro_pattern)))
        
        if not ro_segments:
            raise ValueError(f"No Romanian segments found for chapter {chapter_number}")
        
        # Check for missing translations
        missing_segments = []
        combined_content = []
        total_en_words = 0
        total_ro_words = 0
        
        for ro_file in ro_segments:
            with open(ro_file, 'r', encoding='utf-8') as f:
                content = f.read()
            
            # Remove metadata header
            lines = content.split('\n')
            if lines[0].strip().startswith('<!--'):
                for j, line in enumerate(lines):
                    if '-->' in line:
                        content = '\n'.join(lines[j+1:]).strip()
                        break
            
            if not content or content.isspace():
                missing_segments.append(ro_file.name)
                console.print(f"[yellow]WARNING  Missing translation: {ro_file.name}[/yellow]")
                # Add placeholder
                combined_content.append(f"[MISSING TRANSLATION: {ro_file.name}]")
            else:
                combined_content.append(content)
                total_ro_words += self.count_words(content)
        
        if missing_segments and len(missing_segments) == len(ro_segments):
            raise ValueError(f"No translations found for chapter {chapter_number}")
        
        # Get English word count for comparison
        en_pattern = f"*Chapter_{chapter_number}_seg*_of_*.md"
        en_segments = list(self.dirs['02_en_segments'].glob(en_pattern))
        for en_file in en_segments:
            with open(en_file, 'r', encoding='utf-8') as f:
                total_en_words += self.count_words(f.read())
        
        # Get chapter name from the first segment filename
        # E.g., "17_Chapter_16_seg01_of_03_ro.md" -> extract "Chapter 16"
        first_segment_name = ro_segments[0].name
        # Extract everything between the first underscore and "_seg"
        start_idx = first_segment_name.find('_') + 1
        end_idx = first_segment_name.find('_seg')
        chapter_name = first_segment_name[start_idx:end_idx].replace('_', ' ')
        
        # Create the chapter heading and combine segments
        chapter_heading = f"# {chapter_name}\n\n"
        final_content = chapter_heading + '\n\n'.join(combined_content)
        
        # Create combined chapter file - get prefix from segment filename
        # E.g., "17_Chapter_16_seg01_of_03_ro.md" -> "17_Chapter_16_ro.md"
        prefix = first_segment_name.split('_seg')[0].replace('_ro', '')  # "17_Chapter_16"
        ro_filename = f"{prefix}_ro.md"
        ro_path = self.dirs['04_ro_chapters'] / ro_filename
        
        with open(ro_path, 'w', encoding='utf-8') as f:
            f.write(final_content)
        
        # Create backup
        backup_dir = self.dirs['07_backup'] / 'chapters' / datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ro_path, backup_dir / ro_filename)
        
        
        console.print(f"\n[green]Chapter {chapter_number} combined![/green]")
        console.print(f"OK Combined {len(ro_segments)} segments")
        console.print(f"OK Total words: {total_ro_words} (EN: {total_en_words})")
        
        if missing_segments:
            console.print(f"[yellow]WARNING  {len(missing_segments)} segment(s) still need translation[/yellow]")
        else:
            console.print(f"OK All segments translated")
        
        console.print(f"OK Saved: {ro_filename}")
        console.print(f"OK Backup created")

    def combine_all_chapters(self):
        """Create EPUB from all translated chapters in 04_ro_chapters directory."""
        console.print("[blue]Creating EPUB from all translated chapters...[/blue]")
        
        # Find all Romanian chapter files
        ro_chapters = list(self.dirs['04_ro_chapters'].glob('*_ro.md'))
        
        if not ro_chapters:
            raise ValueError("No translated chapters found in 04_ro_chapters/. Translate some chapters first.")
        
        # Sort chapters by filename number
        def extract_chapter_num(filepath):
            filename = filepath.name
            # Extract number from start of filename (e.g., "17_" -> 17)
            match = re.match(r'^(\d+)_', filename)
            return int(match.group(1)) if match else 999
        
        ro_chapters.sort(key=extract_chapter_num)
        
        console.print(f"[green]Found {len(ro_chapters)} translated chapters:[/green]")
        for chapter_file in ro_chapters:
            console.print(f"  • {chapter_file.name}")
        
        # Create EPUB directly
        console.print(f"\n[blue]Creating final EPUB...[/blue]")
        self.create_epub()
        
        console.print(f"\n[green]✅ EPUB created successfully from {len(ro_chapters)} chapters![/green]")

    def create_epub(self):
        """Create final Romanian EPUB."""
        console.print("[blue]Creating final Romanian EPUB...[/blue]")
        
        config = self.load_config()
        log = self.load_log()
        
        if not config or 'book_name' not in config:
            raise ValueError("No project configuration found. Run --init first.")
        
        # Find all Romanian chapters
        ro_chapters = list(self.dirs['04_ro_chapters'].glob('*_ro.md'))
        
        if not ro_chapters:
            raise ValueError("No Romanian chapters found. Use --combine-chapter first.")
        
        # Sort chapters by filename number
        def extract_chapter_num(filepath):
            filename = filepath.name
            # Extract number from start of filename (e.g., "17_" -> 17)
            match = re.match(r'^(\d+)_', filename)
            return int(match.group(1)) if match else 999
        
        ro_chapters.sort(key=extract_chapter_num)
        
        console.print(f"[green]Found {len(ro_chapters)} translated chapters to include in EPUB[/green]")
        
        # Create EPUB
        book = epub.EpubBook()
        
        # Set metadata
        book.set_identifier(f"{config['book_name']}_ro")
        book.set_title(f"{config['book_name']} (Romanian)")
        book.set_language('ro')
        
        # Add chapters
        chapters = []
        for i, ro_file in enumerate(ro_chapters):
            with open(ro_file, 'r', encoding='utf-8') as f:
                content = f.read()
            
            # Remove metadata header
            lines = content.split('\n')
            if lines[0].strip().startswith('<!--'):
                for j, line in enumerate(lines):
                    if '-->' in line:
                        content = '\n'.join(lines[j+1:]).strip()
                        break
            
            # Create chapter
            chapter_title = ro_file.stem.replace('_ro', '').replace('_', ' ').title()
            chapter = epub.EpubHtml(title=chapter_title, file_name=f'chapter_{i+1}.xhtml', lang='ro')
            chapter.content = f'<h1>{chapter_title}</h1>' + content.replace('\n\n', '</p><p>').replace('\n', '<br/>')
            chapter.content = f'<p>{chapter.content}</p>'
            
            book.add_item(chapter)
            chapters.append(chapter)
        
        # Define table of contents
        book.toc = chapters
        
        # Add navigation
        book.add_item(epub.EpubNcx())
        book.add_item(epub.EpubNav())
        
        # Create spine
        book.spine = ['nav'] + chapters
        
        # Save EPUB
        output_filename = f"{config['book_name']}_RO.epub"
        output_path = self.dirs['05_ro_full_epub'] / output_filename
        
        epub.write_epub(str(output_path), book, {})
        
        console.print(f"\n[green]Romanian EPUB created![/green]")
        console.print(f"OK Included {len(chapters)} chapters")
        console.print(f"OK Saved: {output_filename}")
        console.print(f"OK Location: {output_path}")

    def quick_check(self, chapter_number: int):
        """Quick check without spoilers."""
        console.print(f"[blue]Quick check for Chapter {chapter_number}...[/blue]")
        
        # Find segments
        en_pattern = f"*Chapter_{chapter_number}_seg*_of_*.md"
        ro_pattern = f"*Chapter_{chapter_number}_seg*_of_*_ro.md"
        
        en_segments = sorted(list(self.dirs['02_en_segments'].glob(en_pattern)))
        ro_segments = sorted(list(self.dirs['03_ro_segments'].glob(ro_pattern)))
        
        if not en_segments:
            raise ValueError(f"No segments found for chapter {chapter_number}")
        
        console.print(f"\n[cyan]Chapter {chapter_number} Segment Preview (First 10 words)[/cyan]")
        console.print("-" * 60)
        
        for i, en_file in enumerate(en_segments):
            # Read English segment
            with open(en_file, 'r', encoding='utf-8') as f:
                en_content = f.read()
            
            en_words = en_content.split()[:10]
            en_preview = ' '.join(en_words) + "..."
            
            # Find Romanian segment
            ro_file = None
            for ro_f in ro_segments:
                if self._segments_match(en_file.name, ro_f.name):
                    ro_file = ro_f
                    break
            
            if ro_file and ro_file.exists():
                with open(ro_file, 'r', encoding='utf-8') as f:
                    ro_content = f.read()
                
                # Remove metadata
                ro_lines = ro_content.split('\n')
                if ro_lines[0].strip().startswith('<!--'):
                    for j, line in enumerate(ro_lines):
                        if '-->' in line:
                            ro_content = '\n'.join(ro_lines[j+1:]).strip()
                            break
                
                if ro_content.strip():
                    ro_words = ro_content.split()[:10]
                    ro_preview = ' '.join(ro_words) + "..."
                else:
                    ro_preview = "[NOT TRANSLATED]"
            else:
                ro_preview = "[NOT TRANSLATED]"
            
            console.print(f"Segment {i+1}: EN: \"{en_preview}\"")
            console.print(f"          RO: \"{ro_preview}\"")
            console.print()

    def backup_progress(self, chapter_number: int = None):
        """Backup current progress."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        if chapter_number is not None:
            console.print(f"[blue]Backing up Chapter {chapter_number}...[/blue]")
            backup_dir = self.dirs['07_backup'] / f'chapter_{chapter_number}' / timestamp
        else:
            console.print("[blue]Backing up all progress...[/blue]")
            backup_dir = self.dirs['07_backup'] / 'full_backup' / timestamp
        
        backup_dir.mkdir(parents=True, exist_ok=True)
        
        if chapter_number is not None:
            # Backup specific chapter
            pattern = f"*Chapter_{chapter_number}_*"
            
            for source_dir in ['03_ro_segments', '04_ro_chapters']:
                src_dir = self.dirs[source_dir]
                dest_dir = backup_dir / source_dir
                dest_dir.mkdir(exist_ok=True)
                
                for file in src_dir.glob(pattern):
                    shutil.copy2(file, dest_dir / file.name)
        else:
            # Backup everything
            for dir_name in ['03_ro_segments', '04_ro_chapters', '06_tracking']:
                src_dir = self.dirs[dir_name]
                dest_dir = backup_dir / dir_name
                
                if src_dir.exists():
                    shutil.copytree(src_dir, dest_dir, dirs_exist_ok=True)
        
        console.print(f"OK Backup created: {backup_dir.name}")

    def show_progress(self):
        """Show overall translation progress."""
        console.print("[blue]Overall Translation Progress[/blue]")
        
        # Count actual files in 01_en_chapters folder
        chapter_files = list(self.dirs['01_en_chapters'].glob('*.md'))
        if not chapter_files:
            console.print("[yellow]No chapter files found. Run --extract-chapters first.[/yellow]")
            return
        
        total_chapters = len(chapter_files)
        log = self.load_log()
        segmented_chapters = 0
        combined_chapters = 0
        total_segments = 0
        translated_segments = 0
        
        next_chapter = None
        
        console.print("=" * 40)
        
        # Create mapping from physical files to identifiers
        file_identifiers = []
        for file_path in chapter_files:
            filename = file_path.name
            if filename.startswith('000'):
                # Metadata file - extract number to create meta_X identifier
                match = re.search(r'(\d+)', filename)
                if match:
                    meta_num = int(match.group(1))
                    file_identifiers.append(f'meta_{meta_num}')
            elif re.match(r'^\d+_', filename):
                # Chapter file - extract chapter number
                match = re.search(r'^(\d+)_', filename)
                if match:
                    chapter_num = int(match.group(1))
                    file_identifiers.append(str(chapter_num))
        
        # Sort identifiers (chapters first, then metadata)
        sorted_identifiers = self._sort_chapter_keys(file_identifiers)
        
        for chapter_num in sorted_identifiers:
            # Skip if not in log (file exists but not processed)
            if chapter_num not in log['chapters']:
                continue
                
            chapter_info = log['chapters'][chapter_num]
            status = chapter_info.get('status', 'extracted')
            
            if 'segments' in chapter_info:
                segmented_chapters += 1
                chapter_segments = chapter_info['segments']
                total_segments += chapter_segments
                
                # Count translated segments
                if chapter_num.startswith('meta_'):
                    # Metadata pattern: filename based
                    filename = chapter_info['filename'].replace('.md', '')
                    ro_pattern = f"{filename}_seg*_of_*_ro.md"
                else:
                    # Chapter pattern: number based
                    ro_pattern = f"{int(chapter_num):02d}_*_seg*_of_*_ro.md"
                ro_files = list(self.dirs['03_ro_segments'].glob(ro_pattern))
                
                chapter_translated = 0
                for ro_file in ro_files:
                    with open(ro_file, 'r', encoding='utf-8') as f:
                        content = f.read()
                    # Remove metadata and check if has content
                    lines = content.split('\n')
                    if lines[0].strip().startswith('<!--'):
                        for j, line in enumerate(lines):
                            if '-->' in line:
                                content = '\n'.join(lines[j+1:]).strip()
                                break
                    if content and not content.isspace():
                        chapter_translated += 1
                
                translated_segments += chapter_translated
                
                if status == 'combined':
                    combined_chapters += 1
                elif chapter_translated < chapter_segments and next_chapter is None:
                    next_chapter = (chapter_num, chapter_segments - chapter_translated)
        
        # Progress bars
        chapter_progress = combined_chapters / total_chapters if total_chapters > 0 else 0
        segment_progress = translated_segments / total_segments if total_segments > 0 else 0
        
        # Visual progress bars
        bar_width = 20
        chapter_filled = int(chapter_progress * bar_width)
        segment_filled = int(segment_progress * bar_width)
        
        chapter_bar = "█" * chapter_filled + "░" * (bar_width - chapter_filled)
        segment_bar = "█" * segment_filled + "░" * (bar_width - segment_filled)
        
        console.print(f"Chapters:  {chapter_bar} {chapter_progress*100:.0f}% ({combined_chapters}/{total_chapters})")
        console.print(f"Segments:  {segment_bar} {segment_progress*100:.0f}% ({translated_segments}/{total_segments})")
        console.print()
        
        console.print(f"Chapters Complete: {combined_chapters}")
        console.print(f"Chapters Segmented: {segmented_chapters}")
        console.print(f"Chapters Not Started: {total_chapters - segmented_chapters}")
        
        if next_chapter:
            chapter_num, missing_segs = next_chapter
            console.print(f"\nNext Chapter to Translate: Chapter {chapter_num} ({missing_segs} segments remaining)")
        elif segmented_chapters < total_chapters:
            next_unsegmented = None
            for chapter_num in self._sort_chapter_keys(log['chapters'].keys()):
                if 'segments' not in log['chapters'][chapter_num]:
                    next_unsegmented = chapter_num
                    break
            if next_unsegmented:
                console.print(f"\nNext Chapter to Segment: Chapter {next_unsegmented}")
        else:
            console.print(f"\n[green]🎉 All chapters processed![/green]")
        
        console.print("=" * 40)


    def open_chapter(self, chapter_number: int):
        """Open chapter with all segments in alternating EN/RO tabs in Sublime Text."""
        console.print(f"[blue]Opening Chapter {chapter_number} in alternating EN/RO layout...[/blue]")
        
        if not self.validate_project_structure():
            return False
            
        # Step 1: Auto-detect RO segments directory
        ro_dir_name, ro_suffix = self._detect_ro_segments_directory()
        if not ro_dir_name:
            console.print("[red]No Romanian segments directory found[/red]")
            return False
            
        console.print(f"[cyan]Using RO directory: {ro_dir_name} (suffix: '{ro_suffix}')[/cyan]")
        
        # Step 2: Resolve chapter by number to sequence
        chapter_file, sequence_number = self._resolve_chapter_by_number(chapter_number)
        if not chapter_file:
            console.print(f"[red]Chapter {chapter_number} not found[/red]")
            self._show_available_chapters()
            return False
            
        console.print(f"[green]Found chapter: {chapter_file.name} (sequence {sequence_number})[/green]")
        
        # Step 3: Collect EN segments for this chapter
        en_segments = self._collect_chapter_segments(sequence_number)
        if not en_segments:
            console.print(f"[red]No segments found for Chapter {chapter_number}[/red]")
            return False
            
        console.print(f"[cyan]Found {len(en_segments)} segments[/cyan]")
        
        # Step 4: Match and create RO segment stubs if needed
        ro_segments = self._match_and_create_ro_segments(en_segments, ro_dir_name, ro_suffix)
        
        # Step 5: Build alternating file list
        files_to_open = self._build_alternating_file_list(chapter_file, en_segments, ro_segments)
        
        # Step 6: Open in Sublime Text
        return self._open_files_in_sublime(files_to_open, chapter_number)

    def _detect_ro_segments_directory(self):
        """Auto-detect Romanian segments directory and suffix."""
        possible_dirs = ['03_ro_segments', '03_row_segments', '04rawsegments']
        
        for dir_name in possible_dirs:
            if dir_name in self.dirs and self.dirs[dir_name].exists():
                # Detect suffix by examining existing files
                files = list(self.dirs[dir_name].glob("*.md"))
                if files:
                    # Extract suffix from filename
                    sample_file = files[0].name
                    # Pattern: XX_CHAPTER_X_segXX_of_XX_SUFFIX.md
                    if sample_file.endswith('_ro.md'):
                        return dir_name, '_ro'
                    elif sample_file.endswith('_RO.md'):
                        return dir_name, '_RO'
                    elif sample_file.endswith('.md'):
                        return dir_name, ''  # No suffix
                return dir_name, '_ro'  # Default suffix
                
        return None, None

    def _resolve_chapter_by_number(self, chapter_number: int):
        """Find chapter file by chapter number using sequence mapping."""
        # Get all chapter files and sort them naturally
        chapter_files = sorted(self.dirs['01_en_chapters'].glob("*.md"))
        
        # Build mapping from sequence to chapter info
        chapter_mapping = {}
        for file_path in chapter_files:
            filename = file_path.name
            # Extract sequence number (first part before underscore)
            if '_' in filename:
                try:
                    seq_str = filename.split('_')[0]
                    seq_num = int(seq_str)
                    
                    # Determine chapter number from filename
                    if '_Chapter_' in filename:
                        # Extract chapter number: XX_Chapter_Y.md
                        chapter_part = filename.split('_Chapter_')[1].split('.')[0]
                        if chapter_part.isdigit():
                            ch_num = int(chapter_part)
                            chapter_mapping[ch_num] = (file_path, seq_num)
                except ValueError:
                    continue
                    
        # Find the requested chapter
        if chapter_number in chapter_mapping:
            return chapter_mapping[chapter_number]
        else:
            return None, None

    def _collect_chapter_segments(self, sequence_number: int):
        """Collect all segments for a chapter by sequence number."""
        pattern = f"{sequence_number:02d}_*_seg*.md"
        segments = list(self.dirs['02_en_segments'].glob(pattern))
        
        # Natural sort by segment number
        def extract_seg_num(filename):
            # Extract segment number from: XX_CHAPTER_Y_segZZ_of_NN.md
            parts = filename.name.split('_seg')
            if len(parts) > 1:
                seg_part = parts[1].split('_')[0]
                try:
                    return int(seg_part)
                except ValueError:
                    return 0
            return 0
            
        segments.sort(key=extract_seg_num)
        return segments

    def _match_and_create_ro_segments(self, en_segments, ro_dir_name, ro_suffix):
        """Match EN segments to RO segments, creating stubs if missing."""
        ro_segments = []
        
        for en_seg in en_segments:
            # Create RO filename: add suffix before extension
            en_name = en_seg.name
            if en_name.endswith('.md'):
                ro_name = en_name[:-3] + ro_suffix + '.md'
            else:
                ro_name = en_name + ro_suffix
                
            ro_path = self.dirs[ro_dir_name] / ro_name
            
            # Create stub if doesn't exist or is empty
            if not ro_path.exists() or ro_path.stat().st_size == 0:
                ro_path.parent.mkdir(parents=True, exist_ok=True)
                if not ro_path.exists():
                    ro_path.write_text('', encoding='utf-8')
                    console.print(f"[yellow]Created stub: {ro_name}[/yellow]")
                    
            ro_segments.append(ro_path)
            
        return ro_segments

    def _build_alternating_file_list(self, chapter_file, en_segments, ro_segments):
        """Build file list in alternating EN/RO order."""
        files = [chapter_file]  # Start with complete chapter
        
        # Add alternating segments
        for en_seg, ro_seg in zip(en_segments, ro_segments):
            files.append(en_seg)
            files.append(ro_seg)
            
        return files

    def _open_files_in_sublime(self, files_to_open, chapter_number):
        """Open files in Sublime Text with proper tab order."""
        import subprocess
        import platform
        import shutil
        
        # Try to find Sublime Text
        sublime_cmd = None
        
        if platform.system() == "Windows":
            # Common Windows locations
            possible_paths = [
                "C:/Program Files/Sublime Text/sublime_text.exe",
                "C:/Program Files/Sublime Text 3/sublime_text.exe",
                "C:/Program Files/Sublime Text 4/sublime_text.exe",
            ]
            for path in possible_paths:
                if Path(path).exists():
                    sublime_cmd = path
                    break
            # Also try PATH
            if not sublime_cmd:
                sublime_cmd = shutil.which("subl") or shutil.which("sublime_text")
        else:
            # Unix-like systems
            sublime_cmd = shutil.which("subl") or shutil.which("sublime_text")
            
        if not sublime_cmd:
            console.print("[yellow]Sublime Text not found, using system default editor[/yellow]")
            return self._open_files_with_default_editor(files_to_open, chapter_number)
            
        # Prepare file paths for command line
        file_paths = [str(f) for f in files_to_open]
        
        console.print(f"[green]Opening {len(files_to_open)} files in Sublime Text:[/green]")
        for i, f in enumerate(files_to_open, 1):
            file_type = "Complete" if i == 1 else ("EN" if i % 2 == 0 else "RO")
            console.print(f"  {i}. [{file_type}] {f.name}")
            
        try:
            # Open all files in Sublime Text
            subprocess.run([sublime_cmd] + file_paths, check=True)
            console.print(f"[green]Chapter {chapter_number} opened in Sublime Text with alternating EN/RO tabs[/green]")
            return True
            
        except subprocess.CalledProcessError as e:
            console.print(f"[red]Error opening Sublime Text: {e}[/red]")
            return self._open_files_with_default_editor(files_to_open, chapter_number)
        except Exception as e:
            console.print(f"[red]Unexpected error: {e}[/red]")
            return False

    def _open_files_with_default_editor(self, files_to_open, chapter_number):
        """Fallback: open files with system default editor."""
        import subprocess
        import platform
        
        console.print(f"[yellow]Opening {len(files_to_open)} files with default editor...[/yellow]")
        
        success_count = 0
        for file_path in files_to_open:
            try:
                if platform.system() == "Windows":
                    os.startfile(file_path)
                elif platform.system() == "Darwin":  # macOS
                    subprocess.call(["open", str(file_path)])
                else:  # Linux and other Unix-like
                    subprocess.call(["xdg-open", str(file_path)])
                success_count += 1
            except Exception as e:
                console.print(f"[red]Failed to open {file_path.name}: {e}[/red]")
                
        if success_count > 0:
            console.print(f"[green]Opened {success_count}/{len(files_to_open)} files successfully[/green]")
            return True
        return False

    def _show_available_chapters(self):
        """Show available chapters for reference."""
        console.print("[yellow]Available chapters:[/yellow]")
        
        chapter_files = sorted(self.dirs['01_en_chapters'].glob("*.md"))
        for i, file_path in enumerate(chapter_files[:10], 1):  # Show first 10
            filename = file_path.name
            # Extract chapter info
            if '_Chapter_' in filename:
                chapter_part = filename.split('_Chapter_')[1].split('.')[0]
                console.print(f"  • Chapter {chapter_part}: {filename}")
            else:
                console.print(f"  • {filename}")
                
        if len(chapter_files) > 10:
            console.print(f"  ... and {len(chapter_files) - 10} more chapters")

    def extract_toc(self):
        """Extract and save table of contents to a file for comparison."""
        console.print("[blue]Extracting table of contents...[/blue]")
        
        if not self.validate_project_structure():
            return False
        
        # Find EPUB file
        epub_files = list(self.dirs['00_en_full_epub'].glob('*.epub'))
        if not epub_files:
            console.print("[red]No EPUB file found in 00_en_full_epub/[/red]")
            return False
        
        epub_file = epub_files[0]
        console.print(f"[cyan]Processing: {epub_file.name}[/cyan]")
        
        try:
            # Load EPUB
            book = epub.read_epub(str(epub_file))
            
            # Build TOC mapping
            toc_mapping = self._build_toc_mapping(book)
            
            if not toc_mapping:
                console.print("[yellow]WARNING: No table of contents found in EPUB[/yellow]")
                return False
            
            # Prepare TOC content for output
            toc_content = []
            toc_content.append("# Table of Contents Analysis")
            toc_content.append(f"EPUB File: {epub_file.name}")
            toc_content.append(f"Extracted on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            toc_content.append(f"Total TOC entries: {len(toc_mapping)}")
            toc_content.append("")
            
            # Categorize entries
            regular_chapters = []
            special_chapters = []
            metadata_entries = []
            
            for filename, info in toc_mapping.items():
                title = info['title']
                is_chapter = info['is_chapter']
                level = info.get('level', 0)
                chapter_type = self._categorize_chapter_type(title)
                
                entry = {
                    'filename': filename,
                    'title': title,
                    'type': chapter_type,
                    'is_chapter': is_chapter,
                    'level': level
                }
                
                if chapter_type == 'regular':
                    regular_chapters.append(entry)
                elif chapter_type == 'special':
                    special_chapters.append(entry)
                else:
                    metadata_entries.append(entry)
            
            # Add summary section
            toc_content.append("## Summary")
            toc_content.append(f"- Regular chapters: {len(regular_chapters)}")
            toc_content.append(f"- Special chapters: {len(special_chapters)}")
            toc_content.append(f"- Metadata entries: {len(metadata_entries)}")
            toc_content.append(f"- Total story chapters: {len(regular_chapters) + len(special_chapters)}")
            toc_content.append("")
            
            # Add detailed sections
            if regular_chapters:
                toc_content.append("## Regular Chapters")
                for i, entry in enumerate(sorted(regular_chapters, key=lambda x: x['title']), 1):
                    indent = "  " * entry['level']
                    toc_content.append(f"{indent}{i:2d}. {entry['title']}")
                    toc_content.append(f"{indent}    File: {entry['filename']}")
                toc_content.append("")
            
            if special_chapters:
                toc_content.append("## Special Chapters")
                for entry in special_chapters:
                    indent = "  " * entry['level']
                    toc_content.append(f"{indent}• {entry['title']}")
                    toc_content.append(f"{indent}  File: {entry['filename']}")
                toc_content.append("")
            
            if metadata_entries:
                toc_content.append("## Metadata/Other Entries")
                for entry in metadata_entries:
                    indent = "  " * entry['level']
                    toc_content.append(f"{indent}• {entry['title']}")
                    toc_content.append(f"{indent}  File: {entry['filename']}")
                toc_content.append("")
            
            # Add comparison section with extracted chapters
            toc_content.append("## Comparison with Extracted Chapters")
            
            # Check what chapters have been extracted
            extracted_files = list(self.dirs['01_en_chapters'].glob('*.md'))
            if extracted_files:
                toc_content.append(f"Extracted chapters found: {len(extracted_files)}")
                toc_content.append("")
                
                for chapter_file in sorted(extracted_files):
                    toc_content.append(f"• {chapter_file.name}")
                
                # Check for potential mismatches
                story_chapters_count = len(regular_chapters) + len(special_chapters)
                if len(extracted_files) != story_chapters_count:
                    toc_content.append("")
                    toc_content.append("WARNING: POTENTIAL MISMATCH DETECTED:")
                    toc_content.append(f"   TOC shows {story_chapters_count} story chapters")
                    toc_content.append(f"   But {len(extracted_files)} chapters were extracted")
                    toc_content.append("   Please verify that all chapters were extracted correctly.")
            else:
                toc_content.append("No chapters extracted yet.")
                toc_content.append("Run --extract-chapters to extract chapters from the EPUB.")
            
            # Save to file
            toc_file = self.dirs['06_tracking'] / "table_of_contents.md"
            with open(toc_file, 'w', encoding='utf-8') as f:
                f.write('\n'.join(toc_content))
            
            # Also save as JSON for programmatic use
            toc_json_file = self.dirs['06_tracking'] / "table_of_contents.json"
            toc_data = {
                'epub_file': epub_file.name,
                'extracted_on': datetime.now().isoformat(),
                'total_entries': len(toc_mapping),
                'regular_chapters': regular_chapters,
                'special_chapters': special_chapters,
                'metadata_entries': metadata_entries,
                'story_chapters_count': len(regular_chapters) + len(special_chapters)
            }
            
            with open(toc_json_file, 'w', encoding='utf-8') as f:
                json.dump(toc_data, f, indent=2, ensure_ascii=False)
            
            console.print(f"[green]Table of contents extracted successfully![/green]")
            console.print(f"   Story chapters: {len(regular_chapters) + len(special_chapters)}")
            console.print(f"   Total entries: {len(toc_mapping)}")
            console.print(f"   Markdown report: {toc_file}")
            console.print(f"   JSON data: {toc_json_file}")
            
            return True
            
        except Exception as e:
            console.print(f"[red]ERROR: Error extracting TOC: {e}[/red]")
            return False

    def compare_chapters(self, chapter_number: int) -> bool:
        """Open English and Romanian chapter files side by side in Sublime Text for comparison."""
        try:
            console.print(f"[blue]Opening Chapter {chapter_number} comparison in Sublime Text...[/blue]")
            
            # Find English chapter file by looking for the actual chapter file pattern
            en_chapter_pattern = f"*Chapter_{chapter_number}.md"
            en_chapter_files = list(self.dirs['01_en_chapters'].glob(en_chapter_pattern))
            
            if not en_chapter_files:
                console.print(f"[red]English chapter {chapter_number} not found (pattern: {en_chapter_pattern})[/red]")
                # Show available chapters
                available_chapters = []
                for f in self.dirs['01_en_chapters'].glob("*Chapter_*.md"):
                    # Extract chapter number from filename like "07_Chapter_5.md"
                    match = re.search(r'Chapter_(\d+)\.md', f.name)
                    if match:
                        available_chapters.append(int(match.group(1)))
                if available_chapters:
                    available_chapters.sort()
                    console.print(f"[yellow]Available chapters: {', '.join(map(str, available_chapters))}[/yellow]")
                return False
            
            en_chapter_file = en_chapter_files[0]  # Take the first match
            
            # Find Romanian chapter file
            ro_filename = en_chapter_file.name.replace('.md', '_ro.md')
            ro_chapter_file = self.dirs['04_ro_chapters'] / ro_filename
            
            # Prepare files to open
            files_to_open = []
            
            # Always add English file
            files_to_open.append(str(en_chapter_file))
            console.print(f"[green]Found English chapter: {en_chapter_file.name}[/green]")
            
            # Add Romanian file if exists, or create placeholder
            if ro_chapter_file.exists():
                files_to_open.append(str(ro_chapter_file))
                console.print(f"[green]Found Romanian chapter: {ro_chapter_file.name}[/green]")
            else:
                # Create placeholder Romanian file if it doesn't exist
                ro_chapter_file.parent.mkdir(parents=True, exist_ok=True)
                with open(ro_chapter_file, 'w', encoding='utf-8') as f:
                    f.write(f"# Chapter {chapter_number} - Romanian Translation\n\n")
                    f.write("<!-- Translation goes here -->\n\n")
                files_to_open.append(str(ro_chapter_file))
                console.print(f"[yellow]Created placeholder Romanian chapter: {ro_chapter_file.name}[/yellow]")
            
            # Open both files in Sublime Text
            if files_to_open:
                import subprocess
                try:
                    # Try different possible Sublime Text executable names
                    sublime_commands = ['subl', 'sublime_text', 'sublime_text.exe', 'sublime', 'code']
                    
                    for cmd in sublime_commands:
                        try:
                            # Open all files at once in Sublime Text
                            result = subprocess.run([cmd] + files_to_open, 
                                                  capture_output=True, text=True, timeout=5)
                            if result.returncode == 0:
                                console.print(f"[green]✅ Opened files in {cmd}[/green]")
                                console.print(f"   English: {en_chapter_file.name}")
                                console.print(f"   Romanian: {ro_chapter_file.name}")
                                return True
                        except (subprocess.TimeoutExpired, FileNotFoundError):
                            continue
                    
                    # If Sublime Text is not found, try default system editor
                    console.print("[yellow]Sublime Text not found, trying default system editor...[/yellow]")
                    import os
                    for file_path in files_to_open:
                        os.startfile(file_path)  # Windows specific
                    console.print("[green]✅ Opened files in default editor[/green]")
                    return True
                    
                except Exception as e:
                    console.print(f"[red]ERROR: Error opening files: {e}[/red]")
                    return False
            else:
                console.print("[red]No files to open[/red]")
                return False
            
        except Exception as e:
            console.print(f"[red]ERROR: Error comparing chapters: {e}[/red]")
            return False

    def verify_chapter(self, chapter_number: int) -> bool:
        """
        Verify chapter translation by showing line counts and first/last lines.
        Quick way to check if translation is complete without opening files.
        """
        try:
            # Find English chapter file
            en_chapter_pattern = f"*Chapter_{chapter_number}.md"
            en_chapter_files = list(self.dirs['01_en_chapters'].glob(en_chapter_pattern))

            if not en_chapter_files:
                console.print(f"[red]English chapter {chapter_number} not found[/red]")
                return False

            en_chapter_file = en_chapter_files[0]

            # Find Romanian chapter file
            ro_filename = en_chapter_file.name.replace('.md', '_ro.md')
            ro_chapter_file = self.dirs['04_ro_chapters'] / ro_filename

            if not ro_chapter_file.exists():
                console.print(f"[red]Romanian chapter {chapter_number} not found. Run --combine-chapter {chapter_number} first.[/red]")
                return False

            # Read files
            with open(en_chapter_file, 'r', encoding='utf-8') as f:
                en_lines = f.readlines()

            with open(ro_chapter_file, 'r', encoding='utf-8') as f:
                ro_lines = f.readlines()

            # Count non-empty lines
            en_non_empty = [line for line in en_lines if line.strip()]
            ro_non_empty = [line for line in ro_lines if line.strip()]

            # Display verification info
            console.print(f"\n[cyan]Chapter {chapter_number} Verification[/cyan]")
            console.print("=" * 70)

            # Line counts
            console.print(f"\n[yellow]Line Counts:[/yellow]")
            console.print(f"  English:  {len(en_lines):4d} total lines ({len(en_non_empty):4d} non-empty)")
            console.print(f"  Romanian: {len(ro_lines):4d} total lines ({len(ro_non_empty):4d} non-empty)")

            # Calculate ratio
            if len(en_non_empty) > 0:
                ratio = len(ro_non_empty) / len(en_non_empty)
                console.print(f"  Ratio:    {ratio:.2f} (Romanian/English)")

                if ratio < 0.5:
                    console.print(f"  [red]WARNING: Romanian much shorter than English[/red]")
                elif ratio > 2.0:
                    console.print(f"  [red]WARNING: Romanian much longer than English[/red]")
                else:
                    console.print(f"  [green]OK Ratio looks good[/green]")

            # First lines preview
            console.print(f"\n[yellow]First Line:[/yellow]")
            if en_non_empty:
                console.print(f"  EN: {en_non_empty[0].strip()[:80]}...")
            if ro_non_empty:
                console.print(f"  RO: {ro_non_empty[0].strip()[:80]}...")

            # Last lines preview
            console.print(f"\n[yellow]Last Line:[/yellow]")
            if en_non_empty:
                console.print(f"  EN: {en_non_empty[-1].strip()[:80]}...")
            if ro_non_empty:
                console.print(f"  RO: {ro_non_empty[-1].strip()[:80]}...")

            console.print("\n" + "=" * 70)

            # Quick status check
            if len(ro_non_empty) == 0:
                console.print("[red]ERROR: Romanian chapter appears empty![/red]")
            elif len(ro_non_empty) < 10:
                console.print("[yellow]WARNING: Romanian chapter seems very short[/yellow]")
            else:
                console.print("[green]OK Translation appears complete[/green]")

            console.print(f"\nFiles:")
            console.print(f"  EN: {en_chapter_file}")
            console.print(f"  RO: {ro_chapter_file}")

            return True

        except Exception as e:
            console.print(f"[red]ERROR: Error verifying chapter: {e}[/red]")
            return False

    def send_to_kindle(self, file_path: str) -> bool:
        """
        Send a file to Kindle via Mailjet

        Args:
            file_path: Path to the file to send (supports EPUB, MD, TXT)

        Returns:
            bool: True if successful, False otherwise
        """
        try:
            import base64
            from mailjet_rest import Client

            # Load email configuration from environment variables
            mailjet_api_key = os.getenv('MAILJET_API_KEY')
            mailjet_secret_key = os.getenv('MAILJET_SECRET_KEY')
            sender_email = os.getenv('MAILJET_SENDER_EMAIL')
            sender_name = os.getenv('MAILJET_SENDER_NAME', 'Book Translator')
            kindle_email = os.getenv('KINDLE_EMAIL')

            if not all([mailjet_api_key, mailjet_secret_key, sender_email, kindle_email]):
                console.print("[red]ERROR: Missing email configuration in .env file[/red]")
                console.print("[yellow]Required: MAILJET_API_KEY, MAILJET_SECRET_KEY, MAILJET_SENDER_EMAIL, KINDLE_EMAIL[/yellow]")
                return False

            # Load book name from config if available
            book_name = 'Book'
            if self.config_file.exists():
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                    book_name = config.get('book_name', 'Book')

            # Check if file exists
            file_path = Path(file_path)
            if not file_path.exists():
                console.print(f"[red]ERROR: File '{file_path}' not found[/red]")
                return False

            # Handle MD files - convert to TXT for Kindle compatibility
            temp_file_created = False
            original_extension = file_path.suffix.lower()

            if original_extension == '.md':
                # Create temporary TXT file
                temp_txt_path = file_path.with_suffix('.txt')
                console.print(f"[yellow]Converting {file_path.name} to TXT for Kindle...[/yellow]")

                # Copy MD content to TXT
                import shutil
                shutil.copy2(file_path, temp_txt_path)

                # Use the TXT file for sending
                file_path = temp_txt_path
                temp_file_created = True

            # Read file content
            with open(file_path, 'rb') as f:
                content = f.read()

            # Encode file content as base64
            content_base64 = base64.b64encode(content).decode('ascii')

            # Determine content type
            extension = file_path.suffix.lower()
            content_types = {
                '.epub': 'application/epub+zip',
                '.txt': 'text/plain'
            }
            content_type = content_types.get(extension, 'application/octet-stream')

            # Get filename
            filename = file_path.name

            # Initialize Mailjet client
            mailjet = Client(
                auth=(mailjet_api_key, mailjet_secret_key),
                version='v3.1'
            )

            # Prepare email data
            data = {
                'Messages': [
                    {
                        "From": {
                            "Email": sender_email,
                            "Name": sender_name
                        },
                        "To": [
                            {
                                "Email": kindle_email
                            }
                        ],
                        "Subject": f"{book_name}: {filename}",
                        "TextPart": f"Attachment: {filename}",
                        "Attachments": [
                            {
                                "ContentType": content_type,
                                "Filename": filename,
                                "Base64Content": content_base64
                            }
                        ]
                    }
                ]
            }

            # Send email
            file_size_mb = len(content) / 1024 / 1024
            console.print(f"[cyan]📤 Sending '{filename}' ({file_size_mb:.2f} MB) to Kindle ({kindle_email})...[/cyan]")

            result = mailjet.send.create(data=data)

            # Clean up temporary file if created
            if temp_file_created:
                try:
                    file_path.unlink()
                    console.print(f"[green]Cleaned up temporary file: {file_path.name}[/green]")
                except Exception as e:
                    console.print(f"[yellow]Warning: Could not delete temp file {file_path.name}: {e}[/yellow]")

            if result.status_code == 200:
                console.print(f"[green]OK Successfully sent! Check your Kindle device.[/green]")
                return True
            else:
                console.print(f"[red]ERROR: Failed to send. Status: {result.status_code}[/red]")
                console.print(f"[red]Response: {result.json()}[/red]")
                return False

        except ImportError:
            console.print("[red]ERROR: Mailjet library not installed. Run: pip install mailjet_rest[/red]")
            return False
        except Exception as e:
            # Clean up temp file if there was an error
            if temp_file_created and file_path.exists():
                try:
                    file_path.unlink()
                except:
                    pass
            console.print(f"[red]ERROR: Error sending to Kindle: {e}[/red]")
            return False


@click.command()
@click.option('--init', 'epub_file', help='Initialize project with EPUB file')
@click.option('--init-empty', 'init_empty', is_flag=True, help='Initialize project structure without EPUB')
@click.option('--extract-chapters', 'extract_chapters', is_flag=True, help='Extract chapters from EPUB')
@click.option('--split-chapter', 'split_chapter', type=int, help='Split specific chapter into segments')
@click.option('--split-all-chapters', 'split_all', is_flag=True, help='Split all chapters into segments')
@click.option('--prepare-manual', 'prepare_manual', type=int, help='Prepare chapter for manual translation')
@click.option('--statistics', 'statistics', help='Generate statistics (chapter number or "all")')
@click.option('--combine-chapter', 'combine_chapter', type=int, help='Combine translated segments into chapter')
@click.option('--combine-all-chapters', 'combine_all', is_flag=True, help='Combine all translated chapters')
@click.option('--create-epub', 'create_epub', is_flag=True, help='Create final Romanian EPUB')
@click.option('--quick-check', 'quick_check', type=int, help='Quick check without spoilers')
@click.option('--backup', 'backup', type=int, help='Backup progress for chapter')
@click.option('--progress', 'progress', is_flag=True, help='Show overall progress')
@click.option('--open-chapter', 'open_chapter', type=int, help='Open chapter file in default editor')
@click.option('--extract-toc', 'extract_toc', is_flag=True, help='Extract and save table of contents')
@click.option('--compare', 'compare_chapter', type=int, help='Compare English and Romanian versions of a chapter')
@click.option('--verify', 'verify_chapter', type=int, help='Verify chapter translation (show line counts and preview)')
@click.option('--sendtokindle', 'send_to_kindle', help='Send file to Kindle (EPUB, MD, TXT)')
@click.option('--project-dir', default='.', help='Project directory path')
def main(epub_file, init_empty, extract_chapters, split_chapter, split_all, prepare_manual,
         statistics, combine_chapter, combine_all, create_epub, quick_check,
         backup, progress, open_chapter, extract_toc, compare_chapter, verify_chapter, send_to_kindle, project_dir):
    """Book Translation System - Manage EPUB translation workflow."""
    
    translator = BookTranslator(project_dir)
    
    try:
        if epub_file:
            translator.init_project(epub_file)
        elif init_empty:
            translator.init_project()
        elif extract_chapters:
            translator.extract_chapters()
        elif split_chapter is not None:
            translator.split_chapter(split_chapter)
        elif split_all:
            translator.split_all_chapters()
        elif prepare_manual is not None:
            translator.prepare_manual_translation(prepare_manual)
        elif statistics:
            if statistics.lower() == 'all':
                translator.generate_statistics()
            else:
                translator.generate_statistics(int(statistics))
        elif combine_chapter is not None:
            translator.combine_chapter(combine_chapter)
        elif combine_all:
            translator.combine_all_chapters()
        elif create_epub:
            translator.create_epub()
        elif quick_check is not None:
            translator.quick_check(quick_check)
        elif backup is not None:
            translator.backup_progress(backup)
        elif progress:
            translator.show_progress()
        elif open_chapter is not None:
            translator.open_chapter(open_chapter)
        elif extract_toc:
            translator.extract_toc()
        elif compare_chapter is not None:
            translator.compare_chapters(compare_chapter)
        elif verify_chapter is not None:
            translator.verify_chapter(verify_chapter)
        elif send_to_kindle:
            translator.send_to_kindle(send_to_kindle)
        else:
            console.print("[red]No command specified. Use --help for options.[/red]")
            sys.exit(1)
            
    except Exception as e:
        console.print(f"[red]Error: {str(e)}[/red]")
        sys.exit(1)


if __name__ == "__main__":
    main()