from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from config import CATEGORIES
from database import KnowledgeBase
from plugins import PluginManager
from utils import safe_title


class FileCache:
    def __init__(self, cache_dir: Path) -> None:
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def save_item(self, category: str, title: str, content: str) -> None:
        category_dir = self.cache_dir / category
        category_dir.mkdir(parents=True, exist_ok=True)
        path = category_dir / f"{safe_title(title)}.txt"
        path.write_text(content, encoding="utf-8", errors="ignore")

    def reset_cache(self, db_path: Optional[Path] = None) -> None:
        if self.cache_dir.exists():
            for item in self.cache_dir.glob("**/*"):
                if item.is_file():
                    item.unlink(missing_ok=True)
            for folder in sorted(self.cache_dir.glob("**/*"), reverse=True):
                if folder.is_dir():
                    folder.rmdir()
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        if db_path is not None and db_path.exists():
            db_path.unlink(missing_ok=True)


class ContentUpdater:
    def __init__(self, kb: KnowledgeBase, cache: Optional[FileCache], plugin_manager: PluginManager) -> None:
        self.kb = kb
        self.cache = cache
        self.plugins = plugin_manager

    def _init_builtin(self) -> int:
        builtin_data: Dict[str, List[tuple[str, str]]] = {
            "survival_techniques": [
                ("Shelter Building Basics", "Emergency shelter uses local materials, insulation, and rain run-off planning."),
                ("Find and Purify Water", "Collect from moving water if possible, then boil, filter, or chemically purify."),
                ("Fire Making Techniques", "Use dry tinder, kindling, and reliable ignition methods; protect flame from wind."),
            ],
            "first_aid_basic": [
                ("Wound Care and Bandaging", "Control bleeding, clean wound with safe water, and use sterile dressings."),
                ("CPR and Recovery Position", "CPR cycles and airway management improve survival chances in cardiac emergency."),
            ],
            "hunting": [
                ("Tracking Animal Signs", "Read tracks, droppings, feeding marks, and movement corridors."),
            ],
            "trapping": [
                ("Snare Construction", "Build legal, humane snares at active animal paths and check frequently."),
            ],
            "fishing": [
                ("Improvised Fishing Methods", "Create hand lines and simple hooks, and fish at dawn/dusk when active."),
            ],
            "building_methods": [
                ("Cob Building Basics", "Cob combines clay, sand, and straw for durable low-tech structures."),
            ],
        }
        added = 0
        for category, entries in builtin_data.items():
            for title, content in entries:
                if self.kb.add_knowledge(category, title, content, source="builtin"):
                    added += 1
                    if self.cache:
                        self.cache.save_item(category, title, content)
        return added

    def _fetch_public_data(self) -> int:
        search_queries: Dict[str, List[str]] = {
            "survival_techniques": ["emergency shelter", "water purification", "fire making"],
            "survival_books": ["bushcraft", "wilderness survival manual"],
            "first_aid_basic": ["first aid basics", "wound care"],
            "first_aid_advanced": ["wilderness trauma", "hypothermia treatment"],
            "hunting": ["survival hunting"],
            "trapping": ["trap placement", "snare"],
            "fishing": ["fish preservation", "improvised fishing"],
            "skinning": ["field dressing", "hide tanning"],
            "plant_growing": ["edible plants", "seed saving"],
            "building_methods": ["natural building", "adobe construction"],
        }
        total_added = 0
        for category, queries in search_queries.items():
            for record in self.plugins.fetch_all(queries, category):
                saved = self.kb.add_knowledge(
                    record["category"],
                    record["title"],
                    record["content"],
                    source=record["source"],
                )
                if saved:
                    total_added += 1
                    if self.cache:
                        self.cache.save_item(record["category"], record["title"], record["content"])
        return total_added

    def _fetch_web_content(self) -> int:
        return self._fetch_public_data()

    def auto_update(self) -> int:
        print(f"[{datetime.now().isoformat(timespec='seconds')}] Running auto-update...")
        total = 0

        builtin = self._init_builtin()
        total += builtin
        self.kb.log_update("builtin", builtin, "success")

        scraped = self._fetch_public_data()
        total += scraped
        self.kb.log_update("plugins", scraped, "success")

        return total

    def export_to_json(self, file_path: Path) -> int:
        import json

        rows = self.kb.get_all()
        file_path.write_text(json.dumps(rows, indent=2, default=str), encoding="utf-8")
        return len(rows)

    def import_from_json(self, file_path: Path, source: str = "import") -> int:
        import json

        payload = json.loads(file_path.read_text(encoding="utf-8"))
        imported = 0
        for item in payload:
            category = item.get("category", "survival_techniques")
            if category not in CATEGORIES:
                continue
            title = item.get("title", "Untitled")
            content = item.get("content", "")
            if self.kb.add_knowledge(category, title, content, source=source):
                imported += 1
        self.kb.log_update("import", imported, "success")
        return imported
