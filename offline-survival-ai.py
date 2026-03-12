def _load_json(path: Path, default: dict = None) -> dict:
    """Load JSON file or return default"""
    if default is None:
        default = {}
    if path.exists():
        try:
            with open(path) as f:
                return json.load(f)
        except:
            return default
    return default
#!/usr/bin/env python3
"""Survival Skills AI Chatbot - Offline, self-updating system"""

import json
import sqlite3
import hashlib
import urllib.request
import urllib.error
import urllib.parse
import re
import time
import threading
from datetime import datetime
from pathlib import Path
from typing import Dict, List

APP_DIR = Path.home() / ".survival_chatbot"
DATASETS_DIR = APP_DIR / "datasets"
DB_PATH = APP_DIR / "knowledge.db"
CACHE_DIR = APP_DIR / "cache"
MEDIA_DIR = APP_DIR / "media"
MEDIA_TRACKING_FILE = APP_DIR / "media_tracking.json"
CUSTOM_SOURCES_FILE = APP_DIR / "custom_sources.json"
SCRAPE_TRACKING_FILE = APP_DIR / "scrape_tracking.json"
UPDATE_INTERVAL = 1800

MEDIA_TYPES = {
    "videos": MEDIA_DIR / "videos",
    "pdfs": MEDIA_DIR / "pdfs",
    "audio": MEDIA_DIR / "audio",
    "documents": MEDIA_DIR / "documents"
}

CATEGORIES = {
    "survival_techniques": "Basic & Advanced Survival Techniques",
    "survival_books": "Survival & Wilderness Books",
    "first_aid_basic": "Basic First Aid",
    "first_aid_advanced": "Advanced First Aid",
    "hunting": "Survival Hunting",
    "trapping": "Animal Trapping",
    "fishing": "Fishing Techniques",
    "skinning": "Skinning & Processing",
    "plant_growing": "Plant & Vegetable Growing",
    "building_methods": "Building Methods (Natural Materials)",
    "wikipedia_survival": "Wikipedia Survival Topics"
}

def _save_json(path: Path, data: dict):
    """Save JSON file safely"""
    try:
        with open(path, 'w') as f:
            json.dump(data, f, indent=2, default=str)
    except Exception as e:
        print(f"    Save error: {e}")

def _clean_html(text: str) -> str:
    """Clean HTML tags and entities"""
    text = re.sub(r'<[^>]+>', '', text)
    for entity, char in [('&nbsp;', ' '), ('&quot;', '"'), ('&amp;', '&'), ('&lt;', '<'), ('&gt;', '>')]:
        text = text.replace(entity, char)
    return text.strip()

def _clean_display_text(text: str) -> str:
    """Clean text for display: remove HTML, SVG, base64 images, and non-printable chars"""
    if not isinstance(text, str):
        text = str(text)
    # Remove <source ...base64...> and <img ...base64...> blocks
    text = re.sub(r'<source[^>]+base64,[^>]+>', '', text, flags=re.IGNORECASE)
    text = re.sub(r'<img[^>]+base64,[^>]+>', '', text, flags=re.IGNORECASE)
    # Remove SVG blocks (inline or as data:image/svg+xml)
    text = re.sub(r'<svg[\s\S]*?</svg>', '', text, flags=re.IGNORECASE)
    text = re.sub(r'data:image/svg\+xml;base64,[^"\'>\s]+', '', text, flags=re.IGNORECASE)
    # Remove all HTML tags/entities
    text = _clean_html(text)
    # Remove any remaining non-printable characters except newlines/tabs
    cleaned = ''.join(c for c in text if c.isprintable() or c in '\n\t')
    # Collapse excessive blank lines
    cleaned = re.sub(r'\n{3,}', '\n\n', cleaned)
    return cleaned.strip()

class KnowledgeBase:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._init_db()
    
    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute('CREATE TABLE IF NOT EXISTS knowledge (id INTEGER PRIMARY KEY, category TEXT, title TEXT UNIQUE, content TEXT, source TEXT, last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP, content_hash TEXT)')
            conn.execute('CREATE TABLE IF NOT EXISTS updates (id INTEGER PRIMARY KEY, timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP, source TEXT, count INTEGER, status TEXT)')
            conn.commit()
    
    def add_knowledge(self, category: str, title: str, content: str, source: str = "manual"):
        """Add knowledge only if not redundant or conflicting. Uses hash and fuzzy similarity. Automatically merges conflicting entries."""
        import difflib
        content_hash = hashlib.md5(content.encode()).hexdigest()
        try:
            with sqlite3.connect(self.db_path) as conn:
                # Check for existing entries with same title/category
                conn.row_factory = sqlite3.Row
                cursor = conn.execute('SELECT * FROM knowledge WHERE category = ? AND title = ?', (category, title))
                row = cursor.fetchone()
                if row:
                    # If hash matches, skip (exact duplicate)
                    if row['content_hash'] == content_hash:
                        return False
                    # Fuzzy similarity check (difflib ratio)
                    ratio = difflib.SequenceMatcher(None, row['content'], content).ratio()
                    if ratio > 0.92:
                        # Highly similar, skip as redundant
                        return False
                    elif ratio < 0.5:
                        # Automatically merge both entries
                        print(f"[Congruency Warning] Conflicting info for '{title}' in '{category}'. Automatically merging.")
                        merged = self._merge_contents(row['content'], content)
                        content = merged
                        content_hash = hashlib.md5(content.encode()).hexdigest()
                # Also check for similar content in same category (not just same title)
                cursor = conn.execute('SELECT title, content FROM knowledge WHERE category = ?', (category,))
                for r in cursor.fetchall():
                    ratio = difflib.SequenceMatcher(None, r['content'], content).ratio()
                    if ratio > 0.97:
                        return False  # Redundant in category
                # Passed checks, add/replace
                conn.execute('INSERT OR REPLACE INTO knowledge (category, title, content, source, content_hash, last_updated) VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)', 
                           (category, title, content, source, content_hash))
                conn.commit()
            return True
        except Exception as e:
            print(f"Error adding knowledge: {e}")
            return False

    def _merge_contents(self, a: str, b: str) -> str:
        """Merge two text blocks, keeping all unique lines and sections."""
        a_lines = set(a.splitlines())
        b_lines = set(b.splitlines())
        merged = list(a_lines | b_lines)
        merged.sort()  # Optional: sort for consistency
        return '\n'.join(merged)
    
    def get_by_category(self, category: str) -> List[Dict]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute('SELECT * FROM knowledge WHERE category = ? ORDER BY last_updated DESC', (category,))
            return [dict(row) for row in cursor.fetchall()]
    
    def search(self, query: str) -> List[Dict]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute('SELECT * FROM knowledge WHERE title LIKE ? OR content LIKE ? ORDER BY last_updated DESC', 
                                (f'%{query}%', f'%{query}%'))
            return [dict(row) for row in cursor.fetchall()]
    
    def log_update(self, source: str, count: int, status: str = "success"):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute('INSERT INTO updates (source, count, status) VALUES (?, ?, ?)', (source, count, status))
            conn.commit()

class FileCache:
    def __init__(self, cache_dir: Path):
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
    
    def save_item(self, category: str, title: str, content: str):
        cat_dir = self.cache_dir / category
        cat_dir.mkdir(exist_ok=True)
        filename = re.sub(r'[<>:"/\\|?*]', '', title)[:100]
        filepath = cat_dir / f"{filename}.txt"
        
        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(f"Title: {title}\nCategory: {CATEGORIES.get(category, category)}\nSaved: {datetime.now().isoformat()}\n{'='*60}\n\n{content}")
        except Exception as e:
            print(f"Cache save error: {e}")

import shutil

import socket

class ContentUpdater:
    def _internet_available(self):
        try:
            socket.create_connection(("8.8.8.8", 53), timeout=2)
            return True
        except Exception:
            return False

    def _search_and_download_new_datasets(self):
        """Search for new off-grid survival datasets on the open internet and download them if found."""
        # Example: Use a static list and simulate a search (could be replaced with a real search API)
        candidate_datasets = [
            {
                'name': 'Off-Grid Survival PDF Collection',
                'url': 'https://archive.org/download/OffGridSurvivalPDFs/OffGridSurvivalPDFs.zip',
                'filename': 'OffGridSurvivalPDFs.zip',
                'size_mb': 30
            },
            {
                'name': 'Homesteading and Bushcraft Texts',
                'url': 'https://archive.org/download/HomesteadingBushcraft/HomesteadingBushcraft.zip',
                'filename': 'HomesteadingBushcraft.zip',
                'size_mb': 40
            }
        ]
        # Only download if keywords match (basic filter)
        keywords = ['survival', 'offgrid', 'bushcraft', 'homestead', 'wilderness', 'prepper', 'self-reliance']
        for d in candidate_datasets:
            if any(k in d['name'].lower() or k in d['filename'].lower() for k in keywords):
                dest = DATASETS_DIR / d['filename']
                if dest.exists():
                    continue
                print(f"[Auto-Discovery] Found new dataset: {d['name']} ({d['size_mb']} MB)")
                try:
                    with urllib.request.urlopen(d['url'], timeout=60) as resp, open(dest, 'wb') as out:
                        shutil.copyfileobj(resp, out)
                    print(f"[Auto-Discovery] Downloaded {d['filename']}.")
                    if dest.suffix == '.zip':
                        import zipfile
                        with zipfile.ZipFile(dest, 'r') as zip_ref:
                            zip_ref.extractall(DATASETS_DIR)
                        print(f"[Auto-Discovery] Extracted {d['filename']}.")
                except Exception as e:
                    print(f"[Auto-Discovery] Failed to download {d['name']}: {e}")
    def _autodownload_offgrid_datasets(self):
        """Automatically search for, warn about, and download known off-grid datasets to the datasets folder with a persistent CLI download bar."""
        import sys, threading, time
        datasets = [
            {
                'name': 'Survival Library (Text)',
                'url': 'https://archive.org/download/SurvivalLibraryText/SurvivalLibraryText.zip',
                'filename': 'SurvivalLibraryText.zip',
                'size_mb': 50
            },
            {
                'name': 'Wilderness Survival Guide (PDF)',
                'url': 'https://archive.org/download/WildernessSurvivalGuide/WildernessSurvivalGuide.pdf',
                'filename': 'WildernessSurvivalGuide.pdf',
                'size_mb': 10
            }
        ]
        total_size = sum(d['size_mb'] for d in datasets)
        print(f"\n[Offline] Downloading off-grid datasets (~{total_size} MB):\n")
        for d in datasets:
            print(f"  - {d['name']} ({d['size_mb']} MB)")
        print(f"[Offline] They will be saved to: {DATASETS_DIR}\n")
        # Warn user if space is low (less than 2x total_size MB free)
        try:
            if DATASETS_DIR.exists():
                stat = shutil.disk_usage(str(DATASETS_DIR))
            else:
                stat = shutil.disk_usage(str(APP_DIR))
            free_mb = stat.free // (1024*1024)
            if free_mb < total_size * 2:
                print(f"[Warning] Low disk space: only {free_mb} MB free, but {total_size} MB will be downloaded.")
        except Exception:
            pass
        DATASETS_DIR.mkdir(parents=True, exist_ok=True)
        num = len(datasets)
        progress = {'current': 0, 'total': num, 'percent': 0, 'status': '', 'done': False}

        def download_worker():
            for idx, d in enumerate(datasets, 1):
                dest = DATASETS_DIR / d['filename']
                if dest.exists():
                    continue
                try:
                    progress['status'] = f"Downloading: {d['name']}"
                    with urllib.request.urlopen(d['url'], timeout=60) as resp, open(dest, 'wb') as out:
                        shutil.copyfileobj(resp, out)
                    if dest.suffix == '.zip':
                        import zipfile
                        with zipfile.ZipFile(dest, 'r') as zip_ref:
                            zip_ref.extractall(DATASETS_DIR)
                except Exception:
                    progress['status'] = f"Failed: {d['name']}"
                    time.sleep(1)
                progress['current'] = idx
                progress['percent'] = int((idx)/num*100)
            progress['done'] = True
            progress['status'] = "All downloads complete."

        def progress_bar():
            bar_len = 30
            while not progress['done']:
                percent = progress['percent']
                filled = int(bar_len * percent // 100)
                sys.stdout.write(f"\r[{'='*filled}{' '*(bar_len-filled)}] {percent:3d}% {progress['status']:<40}")
                sys.stdout.flush()
                time.sleep(0.2)
            sys.stdout.write(f"\r[{'='*bar_len}] 100% {progress['status']:<40}\n")
            sys.stdout.flush()

        t1 = threading.Thread(target=download_worker, daemon=True)
        t2 = threading.Thread(target=progress_bar, daemon=True)
        t1.start()
        t2.start()
        # Return immediately so CLI remains usable; progress bar stays at bottom
    def _process_offgrid_datasets(self):
        """Scan the datasets folder for new text, CSV, or JSON files and add their content to the knowledge base."""
        import csv
        if not DATASETS_DIR.exists():
            DATASETS_DIR.mkdir(parents=True, exist_ok=True)
        for file in DATASETS_DIR.iterdir():
            if file.suffix.lower() in ['.txt', '.md']:
                try:
                    with open(file, encoding='utf-8', errors='ignore') as f:
                        content = f.read()
                    if len(content) > 100:
                        title = f"Dataset: {file.stem}"
                        self.kb.add_knowledge('survival_techniques', title, content, source='offgrid_dataset')
                except Exception as e:
                    print(f"[Dataset] Failed to process {file.name}: {e}")
            elif file.suffix.lower() == '.csv':
                try:
                    with open(file, encoding='utf-8', errors='ignore') as f:
                        reader = csv.reader(f)
                        rows = list(reader)
                    # Flatten CSV rows into text
                    content = '\n'.join([', '.join(row) for row in rows])
                    if len(content) > 100:
                        title = f"Dataset: {file.stem} (CSV)"
                        self.kb.add_knowledge('survival_techniques', title, content, source='offgrid_dataset')
                except Exception as e:
                    print(f"[Dataset] Failed to process {file.name}: {e}")
            elif file.suffix.lower() == '.json':
                try:
                    with open(file, encoding='utf-8', errors='ignore') as f:
                        data = json.load(f)
                    # Convert JSON to readable text
                    content = json.dumps(data, indent=2)
                    if len(content) > 100:
                        title = f"Dataset: {file.stem} (JSON)"
                        self.kb.add_knowledge('survival_techniques', title, content, source='offgrid_dataset')
                except Exception as e:
                    print(f"[Dataset] Failed to process {file.name}: {e}")
        def populate_offline_datasets(self):
            """Autonomously download and process public domain survival books and Wikipedia articles."""
            # Step 1: Autodownload off-grid datasets and warn about disk space
            self._autodownload_offgrid_datasets()
            import os
            import urllib.request
            import gzip
            import shutil
            # --- Project Gutenberg Books ---
            gutenberg_books = [
                {
                    'title': 'Camping and Woodcraft',
                    'author': 'Horace Kephart',
                    'url': 'https://www.gutenberg.org/files/42106/42106-0.txt',
                    'category': 'survival_books'
                },
                {
                    'title': 'The Book of Camping and Woodcraft',
                    'author': 'Horace Kephart',
                    'url': 'https://www.gutenberg.org/files/28255/28255-0.txt',
                    'category': 'survival_books'
                },
                {
                    'title': 'First Aid in the Trenches',
                    'author': 'Edward L. Lynch',
                    'url': 'https://www.gutenberg.org/files/17439/17439-0.txt',
                    'category': 'first_aid_basic'
                }
            ]
            for book in gutenberg_books:
                try:
                    print(f"[Offline] Downloading {book['title']}...")
                    response = urllib.request.urlopen(book['url'], timeout=20)
                    raw = response.read().decode('utf-8', errors='ignore')
                    # Remove Gutenberg header/footer
                    content = raw
                    content = re.sub(r'\*\*\* START OF (THE|THIS) PROJECT GUTENBERG.*?\*\*\*', '', content, flags=re.DOTALL)
                    content = re.sub(r'\*\*\* END OF (THE|THIS) PROJECT GUTENBERG.*', '', content, flags=re.DOTALL)
                    content = content.strip()
                    if len(content) > 500:
                        self.kb.add_knowledge(book['category'], f"{book['title']} by {book['author']}", content, source='gutenberg')
                except Exception as e:
                    print(f"[Offline] Failed to download {book['title']}: {e}")

            # --- Wikipedia Survival Articles (using Wikipedia API) ---
            wikipedia_titles = [
                'Survival_skills', 'Water_purification', 'Shelter_(building)', 'First_aid',
                'Bushcraft', 'Firelighting', 'Navigation', 'Edible_wild_plant', 'Hypothermia',
                'Improvised_weapon', 'Wilderness_medical_emergency', 'Outdoor_cooking'
            ]
            for title in wikipedia_titles:
                try:
                    print(f"[Offline] Downloading Wikipedia article: {title.replace('_', ' ')}...")
                    api_url = f"https://en.wikipedia.org/w/api.php?action=query&prop=extracts&explaintext&format=json&titles={urllib.parse.quote(title)}"
                    req = urllib.request.Request(api_url, headers={
                        'User-Agent': 'Mozilla/5.0',
                        'Accept-Language': 'en-US,en;q=0.9'
                    })
                    with urllib.request.urlopen(req, timeout=15) as resp:
                        data = json.loads(resp.read().decode('utf-8', errors='ignore'))
                    pages = data.get('query', {}).get('pages', {})
                    for pageid, page in pages.items():
                        text = page.get('extract', '').strip()
                        if text and len(text) > 200:
                            self.kb.add_knowledge('wikipedia_survival', f"Wikipedia: {title.replace('_', ' ')}", text, source='wikipedia_api')
                except Exception as e:
                    print(f"[Offline] Failed to download Wikipedia article {title}: {e}")

            # --- Off-grid Datasets Folder ---
            print("[Offline] Scanning for local off-grid datasets...")
            self._process_offgrid_datasets()
        def auto_update(self):
            print(f"[{datetime.now()}] Running auto-update...")
            total = 0
            count = self._init_builtin()
            total += count
            self.kb.log_update("builtin", count, "success")
            print(f"  builtin: +{count}")
            # If internet is available, search for new datasets
            if self._internet_available():
                self._search_and_download_new_datasets()
            # Populate offline datasets (Gutenberg + Wikipedia + local datasets)
            self.populate_offline_datasets()
            # Optionally, still try web scraping (can be commented out if not needed)
            count = self._fetch_web_content()
            total += count
            self.kb.log_update("web", count, "success")
            print(f"  web: +{count}")
            return total
    def __init__(self, kb: KnowledgeBase, cache: FileCache = None):
        self.kb = kb
        self.cache = cache
    
    def _init_builtin(self):
        """Load only full public domain (Gutenberg, Wikipedia) survival knowledge with progress bar."""
        import threading, time
        gutenberg_books = [
            {
                'title': 'Camping and Woodcraft',
                'author': 'Horace Kephart',
                'url': 'https://www.gutenberg.org/files/42106/42106-0.txt',
                'category': 'survival_books'
            },
            {
                'title': 'The Book of Camping and Woodcraft',
                'author': 'Horace Kephart',
                'url': 'https://www.gutenberg.org/files/28255/28255-0.txt',
                'category': 'survival_books'
            },
            {
                'title': 'First Aid in the Trenches',
                'author': 'Edward L. Lynch',
                'url': 'https://www.gutenberg.org/files/17439/17439-0.txt',
                'category': 'first_aid_basic'
            }
        ]
        wikipedia_titles = [
            'Survival_skills', 'Water_purification', 'Shelter_(building)', 'First_aid',
            'Bushcraft', 'Firelighting', 'Navigation', 'Edible_wild_plant', 'Hypothermia',
            'Improvised_weapon', 'Wilderness_medical_emergency', 'Outdoor_cooking'
        ]
        total_items = len(gutenberg_books) + len(wikipedia_titles)
        progress = {'current': 0, 'total': total_items, 'percent': 0, 'status': '', 'done': False}

        def progress_bar():
            import sys
            bar_len = 30
            while not progress['done']:
                percent = int(progress['current'] / progress['total'] * 100)
                filled = int(bar_len * percent // 100)
                sys.stdout.write(f"\r[{'='*filled}{' '*(bar_len-filled)}] {percent:3d}% {progress['status']:<40}")
                sys.stdout.flush()
                time.sleep(0.2)
            sys.stdout.write(f"\r[{'='*bar_len}] 100% {progress['status']:<40}\n")
            sys.stdout.flush()

        def download_worker():
            # Download Gutenberg books
            for book in gutenberg_books:
                try:
                    progress['status'] = f"Downloading: {book['title']}"
                    response = urllib.request.urlopen(book['url'], timeout=20)
                    raw = response.read().decode('utf-8', errors='ignore')
                    # Remove Gutenberg header/footer
                    content = raw
                    content = re.sub(r'\*\*\* START OF (THE|THIS) PROJECT GUTENBERG.*?\*\*\*', '', content, flags=re.DOTALL)
                    content = re.sub(r'\*\*\* END OF (THE|THIS) PROJECT GUTENBERG.*', '', content, flags=re.DOTALL)
                    content = content.strip()
                    if len(content) > 500:
                        self.kb.add_knowledge(book['category'], f"{book['title']} by {book['author']}", content, source='gutenberg')
                except Exception as e:
                    progress['status'] = f"Failed: {book['title']}"
                    time.sleep(1)
                progress['current'] += 1
            # Download Wikipedia articles
            for title in wikipedia_titles:
                try:
                    progress['status'] = f"Wikipedia: {title.replace('_', ' ')}"
                    api_url = f"https://en.wikipedia.org/w/api.php?action=query&prop=extracts&explaintext&format=json&titles={urllib.parse.quote(title)}"
                    req = urllib.request.Request(api_url, headers={
                        'User-Agent': 'Mozilla/5.0',
                        'Accept-Language': 'en-US,en;q=0.9'
                    })
                    with urllib.request.urlopen(req, timeout=15) as resp:
                        data = json.loads(resp.read().decode('utf-8', errors='ignore'))
                    pages = data.get('query', {}).get('pages', {})
                    for pageid, page in pages.items():
                        text = page.get('extract', '').strip()
                        if text and len(text) > 200:
                            self.kb.add_knowledge('wikipedia_survival', f"Wikipedia: {title.replace('_', ' ')}", text, source='wikipedia_api')
                except Exception as e:
                    progress['status'] = f"Failed: Wikipedia {title}"
                    time.sleep(1)
                progress['current'] += 1
            progress['done'] = True
            progress['status'] = "All public domain content loaded."

        t1 = threading.Thread(target=download_worker, daemon=True)
        t2 = threading.Thread(target=progress_bar, daemon=True)
        t1.start()
        t2.start()
        t1.join()
        t2.join()
        return total_items
    
    def _fetch_web_content(self):
        """Scrape survival information from web, including related topics. Suppress all errors from user."""
        try:
            response = urllib.request.urlopen("https://www.google.com", timeout=5)
            if response.status != 200:
                return 0
        except:
            return 0

        search_queries = {
            "survival_techniques": ["emergency shelter construction", "water purification", "fire making", "wilderness navigation"],
            "survival_books": ["survival books", "bushcraft guides", "emergency preparedness", "wilderness manuals"],
            "first_aid_basic": ["first aid CPR", "wound care", "shock management", "basic medical care"],
            "first_aid_advanced": ["wilderness trauma", "hypothermia treatment", "emergency splinting", "advanced medicine"],
            "hunting": ["survival hunting", "animal tracking", "hunting safety", "weapon selection"],
            "trapping": ["snare construction", "trap placement", "animal dispatching", "trap design"],
            "fishing": ["fish identification", "improvised fishing", "fish preservation", "fishing techniques"],
            "skinning": ["field dressing", "hide tanning", "fur processing", "butchering skills"],
            "plant_growing": ["edible plants", "seed saving", "soil preparation", "emergency food"],
            "building_methods": ["adobe construction", "thatch roofing", "cob building", "natural shelter"]
        }

        total_added = 0
        ua = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept-Language': 'en-US,en;q=0.9'
        }

        def get_related_topics(html):
            """Extract related topics from DuckDuckGo search results page."""
            # DuckDuckGo: related searches are in <a class="related-searches__item"> or similar
            related = re.findall(r'<a[^>]+class="related-searches__item"[^>]*>(.*?)</a>', html)
            # Fallback: Google style (not used here, but for future)
            if not related:
                related = re.findall(r'<a[^>]+class="_sQb"[^>]*>(.*?)</a>', html)
            # Clean HTML tags
            return [_clean_html(r) for r in related if len(_clean_html(r)) > 2]

        for category, queries in search_queries.items():
            seen_queries = set()
            for query in queries:
                if query in seen_queries:
                    continue
                seen_queries.add(query)
                try:
                    if hasattr(self, '_interrupt_scraping') and self._interrupt_scraping:
                        return total_added
                    url = f"https://duckduckgo.com/html?q={urllib.parse.quote(query)}"
                    req = urllib.request.Request(url, headers=ua)
                    response = urllib.request.urlopen(req, timeout=8)
                    if response.status != 200:
                        continue
                    content = response.read().decode('utf-8', errors='ignore')
                    links = re.findall(r'<a rel="nofollow" class="result__a" href="(.*?)"', content)
                    snippets = re.findall(r'<span class="result__snippet">(.*?)</span>', content)
                    # Always try to pull the full article for the first link
                    if links:
                        main_url = links[0]
                        if main_url.startswith('//'):
                            main_url = 'https:' + main_url
                        try:
                            main_req = urllib.request.Request(main_url, headers=ua)
                            main_resp = urllib.request.urlopen(main_req, timeout=10)
                            if main_resp.status == 200:
                                main_html = main_resp.read().decode('utf-8', errors='ignore')
                                # Try to extract all <article> or <main> or <body> content, fallback to all <p>
                                article_blocks = re.findall(r'<article[\s\S]*?</article>', main_html, re.I)
                                if not article_blocks:
                                    article_blocks = re.findall(r'<main[\s\S]*?</main>', main_html, re.I)
                                if not article_blocks:
                                    article_blocks = re.findall(r'<body[\s\S]*?</body>', main_html, re.I)
                                if article_blocks:
                                    # Remove all tags, keep text
                                    full_text = '\n\n'.join(_clean_html(block) for block in article_blocks)
                                else:
                                    paras = re.findall(r'<p[^>]*>(.*?)</p>', main_html)
                                    filtered_paras = []
                                    for p in paras:
                                        clean = _clean_html(p)
                                        if len(clean) > 40 and not re.search(r'(cookie|privacy|terms|advert|login|sign up|subscribe|copyright|all rights reserved|menu|footer|header|contact|about|newsletter|share|follow us|related|read more|click here)', clean, re.I):
                                            filtered_paras.append(clean)
                                    full_text = '\n\n'.join(filtered_paras)
                                if len(full_text) > 200:
                                    title = f"{query.title()[:60]} (Full Article)"
                                    if self.kb.add_knowledge(category, title, full_text, source="scraped_web_full"):
                                        total_added += 1
                        except Exception:
                            pass
                    # Still add snippets as fallback/extra
                    for i, snippet in enumerate(snippets[:2]):
                        clean_text = _clean_html(snippet)
                        if len(clean_text) > 50:
                            title = f"{query.title()[:60]} (Result {i+1})"
                            if self.kb.add_knowledge(category, title, clean_text, source="scraped_web"):
                                total_added += 1
                    related_topics = get_related_topics(content)
                    for rel in related_topics[:2]:
                        rel_query = rel.strip()
                        if rel_query and rel_query not in seen_queries:
                            seen_queries.add(rel_query)
                            try:
                                rel_url = f"https://duckduckgo.com/html?q={urllib.parse.quote(rel_query)}"
                                rel_req = urllib.request.Request(rel_url, headers=ua)
                                rel_resp = urllib.request.urlopen(rel_req, timeout=8)
                                if rel_resp.status != 200:
                                    continue
                                rel_content = rel_resp.read().decode('utf-8', errors='ignore')
                                rel_links = re.findall(r'<a rel=\"nofollow\" class=\"result__a\" href=\"(.*?)\"', rel_content)
                                rel_snippets = re.findall(r'<span class=\"result__snippet\">(.*?)</span>', rel_content)
                                if rel_links:
                                    rel_main_url = rel_links[0]
                                    try:
                                        if rel_main_url.startswith('//'):
                                            rel_main_url = 'https:' + rel_main_url
                                        rel_main_req = urllib.request.Request(rel_main_url, headers=ua)
                                        rel_main_resp = urllib.request.urlopen(rel_main_req, timeout=10)
                                        if rel_main_resp.status == 200:
                                            rel_main_html = rel_main_resp.read().decode('utf-8', errors='ignore')
                                            rel_paras = re.findall(r'<p[^>]*>(.*?)</p>', rel_main_html)
                                            rel_full_text = '\n\n'.join(_clean_html(p) for p in rel_paras if len(_clean_html(p)) > 40)
                                            if len(rel_full_text) > 200:
                                                rel_title = f"{rel_query.title()[:60]} (Related Full)"
                                                if self.kb.add_knowledge(category, rel_title, rel_full_text, source="related_web_full"):
                                                    total_added += 1
                                    except Exception:
                                        pass
                                for j, rel_snip in enumerate(rel_snippets[:1]):
                                    rel_clean = _clean_html(rel_snip)
                                    if len(rel_clean) > 50:
                                        rel_title = f"{rel_query.title()[:60]} (Related {j+1})"
                                        if self.kb.add_knowledge(category, rel_title, rel_clean, source="related_web"):
                                            total_added += 1
                                time.sleep(0.3)
                            except Exception:
                                continue
                    time.sleep(0.5)
                except Exception:
                    continue

        # Quick Wikipedia scrape (reduced)
        wikipedia_topics = [
            "Survival_skills", "Water_purification", "Shelter_(building)", "First_aid"
        ]

        print(f"    Processing Wikipedia topics...")
        for topic in wikipedia_topics:
            try:
                if hasattr(self, '_interrupt_scraping') and self._interrupt_scraping:
                    print("    Scraping interrupted.")
                    return total_added

                wiki_url = f"https://en.wikipedia.org/wiki/{topic}"
                req_wiki = urllib.request.Request(wiki_url, headers=ua)
                response_wiki = urllib.request.urlopen(req_wiki, timeout=8)

                if response_wiki.status != 200:
                    continue

                wiki_content = response_wiki.read().decode('utf-8', errors='ignore')
                paragraphs = re.findall(r'<p[^>]*>(.*?)</p>', wiki_content)

                if paragraphs:
                    cleaned_paras = []
                    for para in paragraphs[:3]:
                        cleaned = _clean_html(para)
                        cleaned = re.sub(r'\[\d+\]', '', cleaned)
                        if len(cleaned) > 50:
                            cleaned_paras.append(cleaned)

                    if cleaned_paras:
                        full_wiki_text = "\n\n".join(cleaned_paras)
                        if len(full_wiki_text) > 150:
                            title = f"Wikipedia: {topic.replace('_', ' ')}"
                            if self.kb.add_knowledge("wikipedia_survival", title, full_wiki_text, source="wikipedia"):
                                total_added += 1

                time.sleep(0.3)
            except Exception as e:
                continue

        return total_added
    
    def auto_update(self):
        print(f"[{datetime.now()}] Running auto-update...")
        total = 0
        
        count = self._init_builtin()
        total += count
        self.kb.log_update("builtin", count, "success")
        print(f"  builtin: +{count}")
        
        count = self._fetch_web_content()
        total += count
        self.kb.log_update("web", count, "success")
        print(f"  web: +{count}")
        
        return total

class AutonomousScraper:
    def __init__(self, kb: KnowledgeBase, cache: FileCache, updater: ContentUpdater):
        self.kb = kb
        self.cache = cache
        self.updater = updater
        self.running = False
        self.thread = None
        self.tracking = _load_json(SCRAPE_TRACKING_FILE)
        self.internet_available = False
    
    def _check_internet(self) -> bool:
        for addr in ["https://1.1.1.1", "https://8.8.8.8"]:
            try:
                response = urllib.request.urlopen(addr, timeout=2)
                if response.status == 200:
                    return True
            except:
                pass
        return False
    
    def start(self):
        if self.running:
            return
        self.running = True
        self.thread = threading.Thread(target=self._scrape_loop, daemon=True)
        self.thread.start()
        print("🔄 Background scraper started...")
    
    def stop(self):
        self.running = False
    
    def _scrape_loop(self):
        offline_count = 0
        
        while self.running:
            try:
                has_internet = self._check_internet()
                if has_internet:
                    if not self.internet_available:
                        self.internet_available = True
                        print(f"\n[{datetime.now()}] [*] Internet detected! Background scraper active.")
                        offline_count = 0
                    
                    # Only scrape if it's been more than 2 hours since last scrape
                    # and don't interfere with user operations
                    last_scrape = self.tracking.get('last_background_scrape', 0)
                    current_time = time.time()
                    
                    if current_time - last_scrape > 7200:  # 2 hours
                        print(f"[{datetime.now()}] [*] Background scrape starting...")
                        try:
                            count = self.updater._fetch_web_content()
                            if count > 0:
                                print(f"[{datetime.now()}] [+] Background: +{count} items")
                                self.tracking['last_background_scrape'] = current_time
                                _save_json(SCRAPE_TRACKING_FILE, self.tracking)
                        except Exception as e:
                            print(f"[{datetime.now()}] [-] Background scrape failed: {str(e)[:30]}")
                    
                    # Sleep for 30 minutes between checks
                    for _ in range(180):  # 30 minutes * 60 seconds / 10 second sleep
                        if not self.running:
                            break
                        time.sleep(10)
                        
                else:
                    if self.internet_available:
                        self.internet_available = False
                        print(f"\n[{datetime.now()}] [x] Offline mode...")
                    
                    offline_count += 1
                    if offline_count % 180 == 0:  # Every 30 minutes
                        print(f"[{datetime.now()}] [x] Still offline...")
                    
                    # Sleep for 5 minutes when offline
                    for _ in range(30):  # 5 minutes * 60 seconds / 10 second sleep
                        if not self.running:
                            break
                        time.sleep(10)
                        
            except Exception as e:
                if not self.internet_available:
                    print(f"[{datetime.now()}] [-] Background error: {str(e)[:30]}")
                # Sleep for 1 minute on error
                for _ in range(6):
                    if not self.running:
                        break
                    time.sleep(10)

class CLI:
    def deep_dive_cli(self):
        import threading
        import sys
        print("\n🌊 DEEP DIVE WEB SCRAPE")
        print("This will scrape survival information from the web.")
        print("It may take several minutes and requires internet connection.")
        confirm = input("\n[*] Proceed with deep dive? (y/n): ").strip().lower()
        if confirm != 'y':
            print("[-] Deep dive cancelled.")
            return True
        print("\n[*] Deep dive running in background. You can continue using the app.")
        self._scrape_progress = 0
        self._scrape_done = False
        self._scrape_error = None
        def scrape_task():
            try:
                count = self.updater.auto_update()
                self._scrape_done = True
                self._scrape_progress = 100
                self._scrape_result = count
            except Exception as e:
                self._scrape_error = str(e)
                self._scrape_done = True
        self._scrape_cancel = False
        t = threading.Thread(target=scrape_task, daemon=True)
        t.start()
        # Start a progress bar thread that prints status every second
        def show_progress():
            import time
            bar_len = 20
            while not self._scrape_done:
                # Fake progress bar (since we don't have real progress granularity)
                if self._scrape_progress < 100:
                    self._scrape_progress = min(self._scrape_progress + 1, 99)
                percent = self._scrape_progress
                filled = int(bar_len * percent // 100)
                sys.stdout.write(f"\r[{'='*filled}{' '*(bar_len-filled)}] {percent}% Scraping...")
                sys.stdout.flush()
                time.sleep(1)
            sys.stdout.write(f"\r[{'='*bar_len}] 100% Scraping complete.\n")
            sys.stdout.flush()
        progress_thread = threading.Thread(target=show_progress, daemon=True)
        progress_thread.start()
        # Immediately return to menu so user can keep using the app
        return None

    def __init__(self, kb: KnowledgeBase, updater: ContentUpdater):
        self.kb = kb
        self.updater = updater
        self.menu_options = {
            '1': {'name': 'Search knowledge base', 'action': self.search_cli},
            '2': {'name': 'Browse by category', 'action': self.browse_category},
            '3': {'name': 'Chat with AI', 'action': self.chat_cli},
            '4': {'name': 'View all knowledge', 'action': self.view_all},
            '5': {'name': 'Trigger knowledge update', 'action': self.trigger_update},
            '6': {'name': 'Deep dive web scrape', 'action': self.deep_dive_cli},
            '0': {'name': 'Exit', 'action': None}
        }

    def display_menu(self):
        print(f"\n+-- SURVIVAL AI CHATBOT --+ [{DB_PATH.name}] [{len(CATEGORIES)} categories]")
        print("[1] Search  [2] Browse  [3] Chat  [4] All  [5] Update  [6] Deep dive  [0] Exit\n")

    def confirm_selection(self, option_key: str) -> bool:
        # Remove confirmation for speed
        return option_key in self.menu_options
    
    def print_nav_help(self):
        pass  # Remove for speed
    
    def search_cli(self):
        query = input("\n[*] Search query > ").strip()
        if not query or query.lower() in ['back', 'menu', 'exit']:
            return query.lower() if query.lower() in ['back', 'menu', 'exit'] else None
        
        results = self.kb.search(query)
        if results:
            while True:
                print(f"\n[+] Found {len(results)} result(s):\n")
                for i, item in enumerate(results[:10], 1):
                    print(f"    [{i}] {item['title'][:50]}")
                    print(f"        Category: {item['category']} | Len: {len(item['content'])} chars")
                
                print(f"\n    [0] Go back")
                self.print_nav_help()
                sel = input("\n[*] Select item (0-{}, or 'menu'/'exit'): ".format(min(10, len(results)))).strip().lower()
                
                if sel == 'menu':
                    return 'menu'
                elif sel == 'exit':
                    return 'exit'
                elif sel == '0':
                    return None
                
                try:
                    idx = int(sel) - 1
                    if 0 <= idx < len(results):
                        self.read_item(results[idx])
                    else:
                        print("❌ Invalid selection.")
                except ValueError:
                    print("❌ Invalid input.")
        else:
            print("[-] No results found.")
    
    def browse_category(self):
        while True:
            print("\n[+] Categories:\n")
            cat_keys = list(CATEGORIES.keys())
            for i, key in enumerate(cat_keys, 1):
                print(f"    [{i}] {CATEGORIES[key]}")
            print(f"    [0] Back")

            self.print_nav_help()
            sel = input("\n[*] Select category (0-{}, or 'menu'/'exit'): ".format(len(cat_keys))).strip().lower()

            if sel == 'menu':
                return 'menu'
            elif sel == 'exit':
                return 'exit'
            elif sel == '0':
                return None

            try:
                choice = int(sel)
                if choice == 0:
                    return None
                if 1 <= choice <= len(cat_keys):
                    category = cat_keys[choice - 1]
                    nav = self.show_category_items(category)
                    if nav == 'menu':
                        return 'menu'
                    elif nav == 'exit':
                        return 'exit'
                else:
                    print("❌ Invalid selection.")
            except ValueError:
                print("❌ Invalid input.")
    
    def show_category_items(self, category: str):
        """Show items in category with selection"""
        items = self.kb.get_by_category(category)
        if not items:
            print("[-] No content in this category yet.")
            return None
        
        while True:
            print(f"\n[+] {CATEGORIES[category]} ({len(items)} items):\n")
            for i, item in enumerate(items[:15], 1):
                print(f"    [{i}] {item['title'][:50]}")
            if len(items) > 15:
                print(f"    ... and {len(items) - 15} more")
            print(f"    [0] Back")
            
            self.print_nav_help()
            sel = input("\n[*] Select item (0-{}, or 'menu'/'exit'): ".format(min(15, len(items)))).strip().lower()
            
            if sel == 'menu':
                return 'menu'
            elif sel == 'exit':
                return 'exit'
            elif sel == '0':
                return None
            
            try:
                idx = int(sel) - 1
                if 0 <= idx < len(items):
                    self.read_item(items[idx])
                else:
                    print("❌ Invalid selection.")
            except ValueError:
                print("❌ Invalid input.")
    
    def read_item(self, item: Dict):
        """Display full item content, scrollable with navigation."""
        import os
        content = _clean_display_text(item['content'])
        lines = content.splitlines()
        page_size = 20
        total_lines = len(lines)
        page = 0
        while True:
            os.system('cls' if os.name == 'nt' else 'clear')
            print(f"\n+-- {item['title'][:55]} --+")
            print(f"| Category: {item['category']:<40} |")
            print(f"| Source: {item['source']:<44} |")
            print(f"+{'-'*56}+")
            start = page * page_size
            end = min(start + page_size, total_lines)
            for line in lines[start:end]:
                print(line)
            print(f"\n{'-'*56}")
            print(f"[Page {page+1}/{(total_lines-1)//page_size+1}] [N]ext [B]ack [Q]uit")
            nav = input("[*] Command: ").strip().lower()
            if nav in ('q', 'quit', 'exit'):
                break
            elif nav in ('n', 'next', ''):
                if end >= total_lines:
                    print("[End of article]")
                    input("[*] Press Enter to return...")
                    break
                page += 1
            elif nav in ('b', 'back', 'p', 'prev', 'previous'):
                if page > 0:
                    page -= 1
            else:
                print("[-] Invalid command. Use N, B, or Q.")
    
    def chat_cli(self):
        print("\n[+] Chat Mode (type 'menu' to return to main, 'exit' to quit)\n")
        import re
        last_results = []
        while True:
            query = input("[*] You > ").strip()
            if not query:
                continue
            if query.lower() == 'menu':
                return 'menu'
            if query.lower() == 'exit':
                return 'exit'

            # Category filter: user can type 'cat:category question'
            category = None
            cat_match = re.match(r'cat:([\w_\-]+)\s+(.*)', query, re.I)
            if cat_match:
                category = cat_match.group(1).lower()
                query = cat_match.group(2)

            # Hunt through all knowledge for best match
            with sqlite3.connect(self.kb.db_path) as conn:
                conn.row_factory = sqlite3.Row
                if category and category in CATEGORIES:
                    cursor = conn.execute('SELECT * FROM knowledge WHERE category = ?', (category,))
                else:
                    cursor = conn.execute('SELECT * FROM knowledge')
                all_rows = [dict(row) for row in cursor.fetchall()]

            # Simple keyword match scoring
            keywords = re.findall(r'\w+', query.lower())
            def score(entry):
                text = (entry['title'] + ' ' + entry['content']).lower()
                return sum(1 for k in keywords if k in text)

            scored = [(score(row), row) for row in all_rows]
            scored = [item for item in scored if item[0] > 0]
            scored.sort(reverse=True, key=lambda x: (x[0], x[1]['last_updated']))

            if scored:
                # Show up to 20 top results, grouped by category
                top_n = min(20, len(scored))
                categorized = {}
                for idx, (s, row) in enumerate(scored[:top_n], 1):
                    cat = row['category']
                    if cat not in categorized:
                        categorized[cat] = []
                    categorized[cat].append((idx, row))
                print(f"\n[+] Top {top_n} relevant results, categorized:")
                for cat, items in categorized.items():
                    cat_name = CATEGORIES.get(cat, cat)
                    print(f"\n  {cat_name}:")
                    for idx, row in items:
                        print(f"    [{idx}] {row['title']} (Source: {row['source']})")
                print("")
                # Flatten for selection
                idx_to_row = {}
                for items in categorized.values():
                    for idx, row in items:
                        idx_to_row[idx] = row
                last_results = [idx_to_row[i+1] for i in range(top_n)]
                pick = input(f"[*] Enter 1-{top_n} to view, 'all' for all, or 'menu'/ 'exit': ").strip().lower()
                if pick in ('menu', 'exit'):
                    if pick == 'menu':
                        return 'menu'
                    else:
                        return 'exit'
                if pick == 'all':
                    for idx, row in enumerate(last_results, 1):
                        cat_name = CATEGORIES.get(row['category'], row['category'])
                        print(f"\n+-- {row['title']} --+")
                        print(f"| Category: {cat_name:<40} |\n| Source: {row['source']:<44} |")
                        print(f"+{'-'*56}+")
                        content = _clean_display_text(row['content'])
                        preview = '\n'.join(content.splitlines()[:20])
                        print(preview)
                        print(f"\n{'-'*56}")
                    input("[*] Press Enter to continue...")
                    continue
                if pick.isdigit() and 1 <= int(pick) <= top_n:
                    row = last_results[int(pick)-1]
                    cat_name = CATEGORIES.get(row['category'], row['category'])
                    print(f"\n+-- {row['title']} --+")
                    print(f"| Category: {cat_name:<40} |\n| Source: {row['source']:<44} |")
                    print(f"+{'-'*56}+")
                    content = _clean_display_text(row['content'])
                    preview = '\n'.join(content.splitlines()[:20])
                    print(preview)
                    print(f"\n{'-'*56}")
                    view = input("[*] View full article? (y/n/menu/exit) > ").strip().lower()
                    if view == 'y':
                        self.read_item(row)
                    elif view == 'menu':
                        return 'menu'
                    elif view == 'exit':
                        return 'exit'
                else:
                    print("[-] Invalid selection.")
            else:
                # Fallback: return a random or most recent knowledge entry
                with sqlite3.connect(self.kb.db_path) as conn:
                    conn.row_factory = sqlite3.Row
                    cursor = conn.execute('SELECT * FROM knowledge ORDER BY last_updated DESC LIMIT 1')
                    row = cursor.fetchone()
                    if row:
                        print(f"\n[!] No direct information found for '{query}'. Here's something related from the knowledge base:\n")
                        print(f"[+] Assistant: Based on '{row['title']}' (Source: {row['source']}, Category: {row['category']})\n")
                        print(f"{row['content'][:300]}...\n")
                        view = input("[*] View full article? (y/n/menu/exit) > ").strip().lower()
                        if view == 'y':
                            self.read_item(dict(row))
                        elif view == 'menu':
                            return 'menu'
                        elif view == 'exit':
                            return 'exit'
                    else:
                        print(f"[-] No information available in the knowledge base yet. Please update or scrape data.")
    
    def view_all(self):
        print("\n[+] Knowledge Base Summary:\n")
        total = 0
        for category in CATEGORIES.keys():
            items = self.kb.get_by_category(category)
            cat_name = CATEGORIES[category]
            print(f"    {cat_name}: {len(items)} items")
            total += len(items)
        print(f"\n[+] Total: {total} items in database")
        input("\n[*] Press Enter to continue...")
    
    def trigger_update(self):
        """Trigger knowledge update"""
        print("\n[*] Starting knowledge update...\n")
        try:
            self.updater.auto_update()
        except Exception:
            pass
        print("\n[+] Update complete. Returning to main menu.\n")
        return 'menu'
        print("\n🌊 DEEP DIVE WEB SCRAPE")
        print("This will scrape survival information from the web.")
        print("It may take several minutes and requires internet connection.")
        
        confirm = input("\n[*] Proceed with deep dive? (y/n): ").strip().lower()
        if confirm != 'y':
            print("[-] Deep dive cancelled.")
            return True
        
        print("\n[*] Starting deep dive... (This may take a while)")
        print("[*] Press Ctrl+C to interrupt if needed")
        
        # Set interrupt flag on updater
        self.updater._interrupt_scraping = False
        
        try:
            count = self.updater.auto_update()
            print(f"\n✅ Deep dive complete! Added {count} new items.")
        except KeyboardInterrupt:
            print("\n[!] Deep dive interrupted by user.")
            self.updater._interrupt_scraping = True
        except Exception as e:
            print(f"\n❌ Deep dive failed: {e}")
        finally:
            # Clean up interrupt flag
            if hasattr(self.updater, '_interrupt_scraping'):
                delattr(self.updater, '_interrupt_scraping')
        
        return True
    
    def run(self):
        while True:
            self.display_menu()
            choice = input("[*] > ").strip()
            if not self.confirm_selection(choice):
                print("[-] Invalid choice.")
                continue
            if choice == '0':
                print("\n[+] Goodbye!")
                break
            action = self.menu_options[choice]['action']
            if action:
                try:
                    result = action()
                    if result == 'menu':
                        continue
                    elif result == 'exit':
                        print("\n[+] Exiting...")
                        return
                except Exception as e:
                    print(f"❌ Error: {e}")
            else:
                print("[-] Invalid choice.")

if __name__ == '__main__':
    print(f"\n+-- SURVIVAL SKILLS AI CHATBOT --+\n[*] Database: {DB_PATH}\n[+] Cache: {CACHE_DIR}\n[x] Categories: {len(CATEGORIES)}\n")
    APP_DIR.mkdir(exist_ok=True)
    kb = KnowledgeBase(DB_PATH)
    cache = FileCache(CACHE_DIR)
    updater = ContentUpdater(kb, cache)
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.execute('SELECT COUNT(*) FROM knowledge')
        if cursor.fetchone()[0] == 0:
            print("\nInitializing knowledge base...")
            updater.auto_update()
    autonomous_scraper = AutonomousScraper(kb, cache, updater)
    autonomous_scraper.start()
    print("\n🔄 Background scraper running\n")
    try:
        cli = CLI(kb, updater)
        cli.run()
    finally:
        autonomous_scraper.stop()
        print("\n👋 Goodbye!")
