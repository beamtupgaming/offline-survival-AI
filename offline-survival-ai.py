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

APP_DIR: Path = Path.home() / ".survival_chatbot"
DB_PATH: Path = APP_DIR / "knowledge.db"
CACHE_DIR: Path = APP_DIR / "cache"
MEDIA_DIR: Path = APP_DIR / "media"
MEDIA_TRACKING_FILE: Path = APP_DIR / "media_tracking.json"
CUSTOM_SOURCES_FILE: Path = APP_DIR / "custom_sources.json"
SCRAPE_TRACKING_FILE: Path = APP_DIR / "scrape_tracking.json"
UPDATE_INTERVAL = 1800

MEDIA_TYPES: Dict[str, Path] = {
    "videos": MEDIA_DIR / "videos",
    "pdfs": MEDIA_DIR / "pdfs",
    "audio": MEDIA_DIR / "audio",
    "documents": MEDIA_DIR / "documents"
}

CATEGORIES: Dict[str, str] = {
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

def _save_json(path: Path, data: dict) -> None:
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
    """Clean text for display, removing non-printable characters"""
    if not isinstance(text, str):
        text = str(text)
    # Remove non-printable characters except newlines and tabs
    cleaned: str = ''.join(c for c in text if c.isprintable() or c in '\n\t')
    return cleaned.strip()

class KnowledgeBase:
    def enrich_short_entry(self, category: str, title: str, min_length: int = 400) -> bool:
        """If an entry is too short, try to fetch and update with full Wikipedia/public domain content."""
        import urllib.request, json, re
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute('SELECT * FROM knowledge WHERE category = ? AND title = ?', (category, title))
            row = cursor.fetchone()
            if not row or len(row['content']) >= min_length:
                return False
            topic: str = title.split(' --')[0].replace(' ', '_')
            api_url: str = f"https://en.wikipedia.org/w/api.php?action=query&prop=extracts&explaintext&format=json&titles={urllib.parse.quote(topic)}"
            req = urllib.request.Request(api_url, headers={
                'User-Agent': 'Mozilla/5.0',
                'Accept-Language': 'en-US,en;q=0.9'
            })
            try:
                with urllib.request.urlopen(req, timeout=15) as resp:
                    if resp.status != 200:
                        return False
                    data = json.loads(resp.read().decode('utf-8', errors='ignore'))
                pages = data.get('query', {}).get('pages', {})
                for pageid, page in pages.items():
                    text = page.get('extract', '').strip()
                    if text and len(text) > min_length:
                        conn.execute('UPDATE knowledge SET content = ?, source = ? WHERE id = ?', (text, 'wikipedia_api', row['id']))
                        conn.commit()
                        return True
            except Exception:
                return False
        return False
    def __init__(self, db_path: Path) -> None:
        self.db_path: Path = db_path
        self._init_db()
    
    def _init_db(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute('CREATE TABLE IF NOT EXISTS knowledge (id INTEGER PRIMARY KEY, category TEXT, title TEXT UNIQUE, content TEXT, source TEXT, last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP, content_hash TEXT)')
            conn.execute('CREATE TABLE IF NOT EXISTS updates (id INTEGER PRIMARY KEY, timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP, source TEXT, count INTEGER, status TEXT)')
            conn.commit()
    
    def add_knowledge(self, category: str, title: str, content: str, source: str = "manual") -> bool:
        content_hash: str = hashlib.md5(content.encode()).hexdigest()
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute('INSERT OR REPLACE INTO knowledge (category, title, content, source, content_hash, last_updated) VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)', 
                           (category, title, content, source, content_hash))
                conn.commit()
            return True
        except Exception as e:
            print(f"Error adding knowledge: {e}")
            return False
    
    def get_by_category(self, category: str) -> List[Dict]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute('SELECT * FROM knowledge WHERE category = ? ORDER BY last_updated DESC', (category,))
            return [dict(row) for row in cursor.fetchall()]
    
    def search(self, query: str) -> List[Dict]:
        words: List[str] = query.lower().split()
        if not words:
            return []
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            # Build conditions for each word
            conditions = []
            params = []
            for word in words:
                if len(word) > 1:  # Search words longer than 1 char
                    conditions.append("(LOWER(title) LIKE ? OR LOWER(content) LIKE ? OR LOWER(category) LIKE ?)")
                    params.extend([f'%{word}%', f'%{word}%', f'%{word}%'])
            if not conditions:
                return []
            sql: str = f'SELECT * FROM knowledge WHERE {" OR ".join(conditions)} ORDER BY last_updated DESC'
            cursor = conn.execute(sql, params)
            results: List[Dict] = [dict(row) for row in cursor.fetchall()]
            print(f"DEBUG: Search for '{query}' found {len(results)} results")  # Temporary debug
            return results
    
    def log_update(self, source: str, count: int, status: str = "success") -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute('INSERT INTO updates (source, count, status) VALUES (?, ?, ?)', (source, count, status))
            conn.commit()

class FileCache:
    def __init__(self, cache_dir: Path) -> None:
        self.cache_dir: Path = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
    
    def save_item(self, category: str, title: str, content: str) -> None:
        cat_dir: Path = self.cache_dir / category
        cat_dir.mkdir(exist_ok=True)
        filename: str = re.sub(r'[<>:"/\\|?*]', '', title)[:100]
        filepath: Path = cat_dir / f"{filename}.txt"
        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(f"Title: {title}\nCategory: {CATEGORIES.get(category, category)}\nSaved: {datetime.now().isoformat()}\n{'='*60}\n\n{content}")
        except Exception:
            pass

    def reset_cache(self) -> None:
        """Delete all files and folders in the cache directory and remove the database file."""
        import shutil
        if self.cache_dir.exists():
            for item in self.cache_dir.iterdir():
                try:
                    if item.is_dir():
                        shutil.rmtree(item)
                    else:
                        item.unlink()
                except Exception:
                    pass
        # Remove the database file to force a fresh scrape on next startup
        if DB_PATH.exists():
            try:
                DB_PATH.unlink()
            except Exception:
                pass

class ContentUpdater:
    def __init__(self, kb: KnowledgeBase, cache: FileCache = None) -> None:
        self.kb: KnowledgeBase = kb
        self.cache: FileCache = cache
    
    def _init_builtin(self) -> int:
        """Load built-in survival knowledge"""
        builtin_data: Dict[str, List[tuple[str, str]]] = {
            "survival_techniques": [
                ("Shelter Building Basics", "Key principles of emergency shelter construction..."),
                ("Find & Purify Water", "Methods to locate water sources and make it drinkable..."),
                ("Fire Making Techniques", "Traditional and modern methods for starting fire..."),
                ("Navigation Without Tools", "Using sun, stars, and terrain for direction..."),
            ],
            "survival_books": [
                ("Alone in the Wilderness", "Classic survival manual by Tom Brown Jr..."),
                ("The Survivor's Guide to Emergency Preparedness", "Comprehensive reference..."),
                ("Bushcraft 101", "Introduction to outdoor survival skills..."),
            ],
            "first_aid_basic": [
                ("CPR & Recovery Position", "Life-saving cardiopulmonary resuscitation..."),
                ("Wound Care & Bandaging", "Treating cuts, scrapes, and minor injuries..."),
                ("Shock Management", "Recognizing and treating shock symptoms..."),
            ],
            "first_aid_advanced": [
                ("Wilderness Trauma Management", "Advanced injury treatment in remote areas..."),
                ("Hypothermia & Heat Exhaustion", "Treating temperature-related emergencies..."),
                ("Improvised Splinting", "Creating effective emergency splints..."),
            ],
            "hunting": [
                ("Hunting Ethics & Safety", "Responsible hunting practices..."),
                ("Tracking Animal Signs", "Identifying trails and behavior patterns..."),
                ("Basic Weapon Selection", "Choosing tools for survival hunting..."),
            ],
            "trapping": [
                ("Snare Construction", "Building effective animal traps..."),
                ("Trap Placement Strategy", "Locating high-probability areas..."),
                ("Ethical Dispatching", "Humane kill methods..."),
            ],
            "fishing": [
                ("Fish Identification", "Recognizing edible freshwater & saltwater species..."),
                ("Improvised Fishing Methods", "Making hooks, lines, and nets..."),
                ("Fish Preservation", "Smoking, drying, and storing fish..."),
            ],
            "skinning": [
                ("Field Dressing Game", "Initial processing of harvested animals..."),
                ("Skinning Techniques", "Proper fur and hide removal..."),
                ("Hide Tanning Basics", "Traditional curing and tanning methods..."),
            ],
            "plant_growing": [
                ("Emergency Food Plants", "Easy-to-grow edible plants..."),
                ("Seed Saving & Storage", "Preserving seeds for future seasons..."),
                ("Soil Preparation", "Building productive soil from natural materials..."),
            ],
            "building_methods": [
                ("Adobe/Clay Construction", "Building with earth and water..."),
                ("Grass & Thatch Roofing", "Natural waterproofing materials..."),
                ("Cob Building Basics", "Mixing clay, straw, and sand for structures..."),
            ]
        }
        
        for category, entries in builtin_data.items():
            for title, content in entries:
                self.kb.add_knowledge(category, title, content, source="builtin")
        return len(builtin_data)

    def _fetch_public_data(self) -> int:
        """Search and add survival information from public datasets only (e.g., Project Gutenberg, OpenLibrary)."""
        search_queries: Dict[str, List[str]] = {
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
        for category, queries in search_queries.items():
            print(f"    Searching public datasets for {CATEGORIES[category]}...")
            for query in queries:
                found = self.search_public_datasets(query, category)
                if found:
                    total_added += 1
        return total_added

    def search_public_datasets(self, query: str, category: str) -> bool:
        """Attempt to find and add information from public datasets (e.g., Project Gutenberg, public domain books, open data portals) for the given query."""
        import urllib.request, urllib.parse, json
        try:
            search_url = f"https://gutendex.com/books/?search={urllib.parse.quote(query)}"
            req = urllib.request.Request(search_url, headers={
                'User-Agent': 'Mozilla/5.0',
                'Accept-Language': 'en-US,en;q=0.9'
            })
            with urllib.request.urlopen(req, timeout=10) as resp:
                if resp.status != 200:
                    return False
                data = json.loads(resp.read().decode('utf-8', errors='ignore'))
            books = data.get('results', [])
            if books:
                for book in books[:1]:
                    title = book.get('title', 'Unknown Title')
                    authors = ', '.join(a['name'] for a in book.get('authors', []))
                    book_url = book.get('formats', {}).get('text/html', book.get('formats', {}).get('text/plain', ''))
                    summary = book.get('subjects', [])
                    content = f"[Public Dataset] {title} by {authors}\nSubjects: {', '.join(summary)}\nRead online: {book_url}"
                    self.kb.add_knowledge(category, f"{query.title()} (Gutenberg: {title})", content, source="project_gutenberg")
                    return True
        except Exception:
            return False
        return False

    def auto_update(self) -> int:
        print(f"[{datetime.now()}] Running auto-update (public data only)...")
        total = 0
        count: int = self._init_builtin()
        total += count
        self.kb.log_update("builtin", count, "success")
        print(f"  builtin: +{count}")
        count: int = self._fetch_public_data()
        total += count
        self.kb.log_update("public_data", count, "success")
        print(f"  public_data: +{count}")
        return total

class AutonomousScraper:
    def __init__(self, kb: KnowledgeBase, cache: FileCache, updater: ContentUpdater) -> None:
        self.kb: KnowledgeBase = kb
        self.cache: FileCache = cache
        self.updater: ContentUpdater = updater
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
    
    def start(self) -> None:
        if self.running:
            return
        self.running = True
        self.thread = threading.Thread(target=self._scrape_loop, daemon=True)
        self.thread.start()
        print("🔄 Background scraper started...")
    
    def stop(self) -> None:
        self.running = False
    
    def _scrape_loop(self) -> None:
        offline_count = 0
        
        while self.running:
            try:
                has_internet: bool = self._check_internet()
                if has_internet:
                    if not self.internet_available:
                        self.internet_available = True
                        print(f"\n[{datetime.now()}] [*] Internet detected! Background scraper active.")
                        offline_count = 0
                    
                    # Only scrape if it's been more than 2 hours since last scrape
                    # and don't interfere with user operations
                    last_scrape = self.tracking.get('last_background_scrape', 0)
                    current_time: float = time.time()
                    
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
    def enrich_all_short_entries(self, min_length: int = 400) -> None:
        """Enrich all short entries in the knowledge base for consistent, detailed output style."""
        print("[Enrichment] Checking for short entries to enrich...")
        with sqlite3.connect(self.kb.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute('SELECT category, title, content FROM knowledge')
            for row in cursor.fetchall():
                if len(row['content']) < min_length:
                    self.kb.enrich_short_entry(row['category'], row['title'], min_length)

    def __init__(self, kb: KnowledgeBase, updater: ContentUpdater) -> None:
        self.kb = kb
        self.updater = updater
        self.menu_options = {
            '1': {'name': 'Search knowledge base', 'action': self.search_cli},
            '2': {'name': 'Browse by category', 'action': self.browse_category},
            '3': {'name': 'Chat with AI', 'action': self.chat_cli},
            '4': {'name': 'View all knowledge', 'action': self.view_all},
            '5': {'name': 'Trigger knowledge update', 'action': self.trigger_update},
            '6': {'name': 'Deep dive web scrape', 'action': self.deep_dive_cli},
            '7': {'name': 'Delete the cache', 'action': self.delete_cache_cli},
            '0': {'name': 'Exit', 'action': None}
        }

    def delete_cache_cli(self) -> None:
        confirm = input("\n[*] Are you sure you want to delete the cache? (y/n): ").strip().lower()
        if confirm == 'y':
            try:
                if hasattr(self, 'updater') and hasattr(self.updater, 'cache') and self.updater.cache:
                    self.updater.cache.reset_cache()
                    print("[+] Cache deleted successfully.")
                else:
                    print("[-] Cache object not available.")
            except Exception as e:
                print(f"[-] Failed to delete cache: {e}")
        else:
            print("[-] Cache deletion cancelled.")
        # Automatically enrich all short entries after initialization
        try:
            self.enrich_all_short_entries(min_length=400)
        except Exception as e:
            print(f"[Enrichment] Warning: {e}")
    
    def display_menu(self) -> None:
        print(f"""
    -- SURVIVAL AI CHATBOT ------------------------------------+
    | Database: {DB_PATH.name} | Categories: {len(CATEGORIES)}                       |
    -------------------------------------------------------+

    [1] Search knowledge  [2] Browse categories  [3] Chat
    [4] View all          [5] Update knowledge   [6] Deep dive
    [7] Delete the cache
    [0] Exit
    """)
    
    def confirm_selection(self, option_key: str) -> bool:
        """Display selected option and ask for confirmation"""
        if option_key not in self.menu_options:
            return False
        
        option_name = self.menu_options[option_key]['name']
        print(f"\n► {option_name}")
        confirm: str = input("Proceed? (y/n): ").strip().lower()
        return confirm == 'y'
    
    def print_nav_help(self) -> None:
        """Print navigation help"""
        print("\n[*] Navigation: 'back'=previous | 'menu'=main menu | 'exit'=quit")
    
    def search_cli(self) -> str | None:
        query: str = input("\n[*] Search query > ").strip()
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
                sel: str = input("\n[*] Select item (0-{}, or 'menu'/'exit'): ".format(min(10, len(results)))).strip().lower()
                
                if sel == 'menu':
                    return 'menu'
                elif sel == 'exit':
                    return 'exit'
                elif sel == '0':
                    return None
                
                try:
                    idx: int = int(sel) - 1
                    if 0 <= idx < len(results):
                        self.read_item(results[idx])
                    else:
                        print("❌ Invalid selection.")
                except ValueError:
                    print("❌ Invalid input.")
        else:
            print("[-] No results found.")
    
    def browse_category(self) -> None | str:
        while True:
            print("\n[+] Categories:\n")
            cat_keys: List[str] = list(CATEGORIES.keys())
            for i, key in enumerate(cat_keys, 1):
                print(f"    [{i}] {CATEGORIES[key]}")
            print(f"    [0] Back")
            
            self.print_nav_help()
            sel: str = input("\n[*] Select category (0-{}, or 'menu'/'exit'): ".format(len(cat_keys))).strip().lower()
            
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
                    category: str = cat_keys[choice - 1]
                    nav: None | str = self.show_category_items(category)
                    if nav == 'menu':
                        return 'menu'
                    elif nav == 'exit':
                        return 'exit'
                else:
                    print("❌ Invalid selection.")
            except ValueError:
                print("❌ Invalid input.")
    
    def show_category_items(self, category: str) -> None | str:
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
            sel: str = input("\n[*] Select item (0-{}, or 'menu'/'exit'): ".format(min(15, len(items)))).strip().lower()
            
            if sel == 'menu':
                return 'menu'
            elif sel == 'exit':
                return 'exit'
            elif sel == '0':
                return None
            
            try:
                idx: int = int(sel) - 1
                if 0 <= idx < len(items):
                    self.read_item(items[idx])
                else:
                    print("❌ Invalid selection.")
            except ValueError:
                print("❌ Invalid input.")
    
    def read_item(self, item: Dict) -> None:
        """Display full item content"""
        print(f"\n+-- {item['title'][:55]} --+")
        print(f"| Category: {item['category']:<40} |")
        print(f"| Source: {item['source']:<44} |")
        print(f"+{'-'*56}+\n")
        content: str = _clean_display_text(item['content'])
        print(f"{content}")
        print(f"\n{'-'*58}")
        input("[*] Press Enter to continue...")
    
    def chat_cli(self) -> str:
        print("\n[+] Chat Mode (type 'menu' to return to main, 'exit' to quit)\n")
        while True:
            query: str = input("[*] You > ").strip()
            if not query:
                continue
            if query.lower() == 'menu':
                return 'menu'
            if query.lower() == 'exit':
                return 'exit'
            
            results = self.kb.search(query)
            if results:
                print(f"\n[+] Assistant: Based on '{results[0]['title']}'\n")
                print(f"{results[0]['content'][:300]}...\n")
                view: str = input("[*] View full article? (y/n/menu/exit) > ").strip().lower()
                if view == 'y':
                    self.read_item(results[0])
                elif view == 'menu':
                    return 'menu'
                elif view == 'exit':
                    return 'exit'
            else:
                print("[-] No information found. Try different keywords.\n")
    
    def view_all(self) -> None:
        print("\n[+] Knowledge Base Summary:\n")
        total = 0
        for category in CATEGORIES.keys():
            items = self.kb.get_by_category(category)
            cat_name = CATEGORIES[category]
            print(f"    {cat_name}: {len(items)} items")
            total += len(items)
        print(f"\n[+] Total: {total} items in database")
        input("\n[*] Press Enter to continue...")
    
    def trigger_update(self) -> bool:
        """Trigger knowledge update"""
        print("\n[*] Starting knowledge update...")
        count: int = self.updater.auto_update()
        print(f"[+] Updated {count} items.")
        return True
    
    def deep_dive_cli(self) -> bool:
        """Perform deep dive web scraping"""
        print("\n🌊 DEEP DIVE WEB SCRAPE")
        print("This will scrape survival information from the web.")
        print("It may take several minutes and requires internet connection.")
        
        confirm: str = input("\n[*] Proceed with deep dive? (y/n): ").strip().lower()
        if confirm != 'y':
            print("[-] Deep dive cancelled.")
            return True
        
        print("\n[*] Starting deep dive... (This may take a while)")
        print("[*] Press Ctrl+C to interrupt if needed")
        
        # Set interrupt flag on updater
        self.updater._interrupt_scraping = False
        
        try:
            count: int = self.updater.auto_update()
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
    
    def run(self) -> None:
        while True:
            self.display_menu()
            choice: str = input("[*] > ").strip()
            
            # Confirm selection
            if not self.confirm_selection(choice):
                print("[-] Operation cancelled.")
                continue
            
            # Execute selected option
            if choice == '0':
                print("\n[+] Goodbye!")
                break
            elif choice in self.menu_options and self.menu_options[choice]['action']:
                try:
                    result = self.menu_options[choice]['action']()
                    # Handle navigation returns
                    if result == 'menu':
                        continue
                    elif result == 'exit':
                        print("\n[+] Exiting...")
                        return
                    # Return to menu for any other result (None, True, etc.)
                    continue
                except Exception as e:
                    print(f"❌ Error: {e}")
                    continue
            else:
                print("[-] Invalid choice.")
                continue

if __name__ == '__main__':
    print(f"""
+------------ SURVIVAL SKILLS AI CHATBOT ----------------+
|     OFFLINE & SELF-UPDATE KNOWLEDGE SYSTEM             |

[*] Database: {DB_PATH}
[+] Cache: {CACHE_DIR}
[x] Categories: {len(CATEGORIES)}
""")

    APP_DIR.mkdir(exist_ok=True)
    # If the database does not exist, build a new one and trigger a fresh scrape
    db_exists = DB_PATH.exists()
    kb = KnowledgeBase(DB_PATH)
    cache = FileCache(CACHE_DIR)
    updater = ContentUpdater(kb, cache)

    if not db_exists:
        print("\nNo database found. Building new knowledge base and scraping public data...")
        builtin_count: int = updater._init_builtin()
        kb.log_update("builtin", builtin_count, "success")
        updater._fetch_public_data()
        kb.log_update("public_data", builtin_count, "success")
    else:
        print("\nInitializing knowledge base...")
        builtin_count: int = updater._init_builtin()
        kb.log_update("builtin", builtin_count, "success")

    autonomous_scraper = AutonomousScraper(kb, cache, updater)
    autonomous_scraper.start()
    print("\n🔄 Background scraper running\n")

    try:
        cli = CLI(kb, updater)
        cli.run()
    finally:
        autonomous_scraper.stop()
        print("\n👋 Goodbye!")
