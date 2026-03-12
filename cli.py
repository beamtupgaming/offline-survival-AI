from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Optional

from config import CATEGORIES, CUSTOM_SOURCES_FILE, FAVORITES_FILE, RECENT_SEARCHES_FILE
from database import KnowledgeBase
from scraper import AutonomousScraper
from updater import ContentUpdater
from utils import chunked, clean_display_text, load_json, save_json, tokenize_query

try:
    from prompt_toolkit import prompt

    HAS_PROMPT_TOOLKIT = True
except Exception:
    HAS_PROMPT_TOOLKIT = False


class CLI:
    def __init__(self, kb: KnowledgeBase, updater: ContentUpdater, scraper: Optional[AutonomousScraper] = None) -> None:
        self.kb = kb
        self.updater = updater
        self.scraper = scraper
        self.chat_style = "field-manual"
        self.recent_searches: List[str] = load_json(RECENT_SEARCHES_FILE, default=[])
        self.favorites: List[int] = load_json(FAVORITES_FILE, default=[])

    def _input(self, label: str) -> str:
        if HAS_PROMPT_TOOLKIT:
            return prompt(label)
        return input(label)

    def _set_user_active(self, active: bool) -> None:
        if self.scraper is not None:
            self.scraper.set_user_active(active)

    def _remember_search(self, query: str) -> None:
        if not query:
            return
        if query in self.recent_searches:
            self.recent_searches.remove(query)
        self.recent_searches.insert(0, query)
        self.recent_searches = self.recent_searches[:20]
        save_json(RECENT_SEARCHES_FILE, self.recent_searches)

    def _save_favorites(self) -> None:
        save_json(FAVORITES_FILE, self.favorites)

    def display_menu(self) -> None:
        print("\n\033[96m-- SURVIVAL AI CHATBOT ----------------------------------\033[0m")
        print("[1] Search knowledge")
        print("[2] Browse categories")
        print("[3] Chat")
        print("[4] View all summary")
        print("[5] Update knowledge")
        print("[6] Deep dive scrape")
        print("[7] Delete cache")
        print("[8] Recent searches")
        print("[9] Favorites")
        print("[10] Export knowledge")
        print("[11] Import knowledge")
        print("[12] Manage custom sources")
        print("[0] Exit")

    def run(self) -> None:
        while True:
            self.display_menu()
            self._set_user_active(True)
            choice = self._input("\n[*] > ").strip().lower()
            nav = None
            try:
                if choice == "0":
                    print("Goodbye.")
                    return
                if choice == "1":
                    nav = self.search_cli()
                elif choice == "2":
                    nav = self.browse_categories()
                elif choice == "3":
                    nav = self.chat_cli()
                elif choice == "4":
                    nav = self.view_all()
                elif choice == "5":
                    nav = self.trigger_update()
                elif choice == "6":
                    nav = self.deep_dive_cli()
                elif choice == "7":
                    nav = self.delete_cache_cli()
                elif choice == "8":
                    nav = self.show_recent_searches()
                elif choice == "9":
                    nav = self.show_favorites()
                elif choice == "10":
                    nav = self.export_cli()
                elif choice == "11":
                    nav = self.import_cli()
                elif choice == "12":
                    nav = self.manage_custom_sources_cli()
                else:
                    print("Invalid choice.")
            finally:
                self._set_user_active(False)

            if nav == "exit":
                print("Goodbye.")
                return

    def search_cli(self) -> Optional[str]:
        query = self._input("\n[*] Search query > ").strip()
        if not query:
            return
        self._remember_search(query)
        results = self.kb.search(query)
        if not results:
            print("No results found.")
            return
        return self.paginated_results(results)

    def paginated_results(self, results: List[Dict]) -> Optional[str]:
        pages = chunked(results, 8)
        page_index = 0
        while True:
            page = pages[page_index]
            print(f"\nResults page {page_index + 1}/{len(pages)}")
            for idx, item in enumerate(page, 1):
                star = "★" if item["id"] in self.favorites else " "
                print(f"[{idx}] {star} {item['title'][:72]} (score={item.get('score', 0):.3f})")

            print("[n] next page  [p] previous page  [f#] favorite toggle  [#] open  [b] back  [menu] main menu  [exit] quit")
            action = self._input("[*] > ").strip().lower()
            if action == "b":
                return
            if action == "menu":
                return "menu"
            if action == "exit":
                return "exit"
            if action == "n" and page_index < len(pages) - 1:
                page_index += 1
                continue
            if action == "p" and page_index > 0:
                page_index -= 1
                continue
            if action.startswith("f") and action[1:].isdigit():
                rel = int(action[1:]) - 1
                if 0 <= rel < len(page):
                    entry_id = page[rel]["id"]
                    if entry_id in self.favorites:
                        self.favorites.remove(entry_id)
                    else:
                        self.favorites.append(entry_id)
                    self._save_favorites()
                continue
            if action.isdigit():
                rel = int(action) - 1
                if 0 <= rel < len(page):
                    nav = self.read_item(page[rel])
                    if nav in {"menu", "exit"}:
                        return nav

    def browse_categories(self) -> Optional[str]:
        keys = list(CATEGORIES.keys())
        while True:
            print("\nCategories:")
            for idx, key in enumerate(keys, 1):
                print(f"[{idx}] {CATEGORIES[key]}")
            print("[0] back")
            value = self._input("[*] Select > ").strip()
            if value == "0":
                return
            if not value.isdigit():
                continue
            choice = int(value)
            if 1 <= choice <= len(keys):
                items = self.kb.get_by_category(keys[choice - 1])
                if not items:
                    print("No entries yet.")
                    continue
                nav = self.paginated_results(items)
                if nav in {"menu", "exit"}:
                    return nav

    def chat_cli(self) -> Optional[str]:
        print("\nChat mode: type 'menu' to return, or 'style' to toggle answer style.")
        while True:
            query = self._input(f"You ({self.chat_style}) > ").strip()
            if query.lower() == "menu":
                return
            if query.lower() == "exit":
                return "exit"
            if query.lower() == "style":
                self.chat_style = "compact" if self.chat_style == "field-manual" else "field-manual"
                print(f"Assistant: chat style set to {self.chat_style}.")
                continue
            if not query:
                continue
            self._remember_search(query)
            results = self.kb.search(query, limit=40)
            if not results:
                print("No information found.")
                continue
            answer = self._generate_answer(query, results)
            print(f"\nAssistant: {answer}")
            print(f"\nAssistant: Found {len(results)} matching article(s).")
            print("Select an article to open full paginated view.")
            nav = self.paginated_results(results)
            if nav in {"menu", "exit"}:
                return nav

    def _generate_answer(self, query: str, results: List[Dict]) -> str:
        tokens = [token for token in tokenize_query(query) if token]
        if not results:
            return "I could not find enough matching material in your local knowledge base."

        candidates: List[tuple[int, str]] = []
        for rank, row in enumerate(results[:8]):
            content = clean_display_text(str(row.get("content", "")))
            sentences = re.split(r"(?<=[.!?])\s+", content)
            for sentence in sentences:
                text = sentence.strip()
                if len(text) < 35:
                    continue
                text_lower = text.lower()
                hit_count = sum(1 for token in tokens if token in text_lower)
                if tokens and hit_count == 0:
                    continue
                score = hit_count * 5 + max(0, 8 - rank)
                candidates.append((score, text))

        if not candidates:
            fallback = clean_display_text(str(results[0].get("content", "")))
            trimmed = fallback[:700].strip()
            if not trimmed:
                return "I found records, but they do not contain enough readable detail to answer directly."
            if self.chat_style == "compact":
                return trimmed
            return "\n".join(
                [
                    "Step-by-step (Field Manual):",
                    f"1. {trimmed}",
                    "Field notes:",
                    "- Validate this guidance against the full source article before acting.",
                ]
            )

        unique_lines: List[str] = []
        seen = set()
        for _score, sentence in sorted(candidates, key=lambda item: item[0], reverse=True):
            if sentence in seen:
                continue
            seen.add(sentence)
            unique_lines.append(sentence)
            if len(unique_lines) >= 6:
                break

        if not unique_lines:
            return "I found relevant documents, but no clear instructions could be extracted."

        if self.chat_style == "compact":
            return " ".join(unique_lines[:3])

        steps = unique_lines[:4]
        notes = unique_lines[4:6]
        if not notes:
            top_sources = [row for row in results[:3] if row.get("title")]
            notes = [f"Cross-check details in: {row['title']}" for row in top_sources]

        lines = ["Step-by-step (Field Manual):"]
        for idx, step in enumerate(steps, 1):
            lines.append(f"{idx}. {step}")
        lines.append("Field notes:")
        for note in notes[:3]:
            lines.append(f"- {note}")
        return "\n".join(lines)

    def view_all(self) -> None:
        total = 0
        print("\nKnowledge Base Summary:")
        for category_key in CATEGORIES:
            count = len(self.kb.get_by_category(category_key))
            total += count
            print(f"- {CATEGORIES[category_key]}: {count}")
        print(f"Total entries: {total}")

    def trigger_update(self) -> None:
        count = self.updater.auto_update()
        print(f"Update complete: {count} new or revised items.")

    def deep_dive_cli(self) -> None:
        confirm = self._input("Run deep dive scrape? (y/n) > ").strip().lower()
        if confirm != "y":
            return
        count = self.updater._fetch_web_content()
        print(f"Deep dive added {count} items.")

    def delete_cache_cli(self) -> None:
        confirm = self._input("Delete cache and database? (y/n) > ").strip().lower()
        if confirm != "y":
            return
        if self.updater.cache is None:
            print("Cache is not configured.")
            return
        self.updater.cache.reset_cache(self.kb.db_path)
        print("Cache and database deleted. Restart the app to rebuild.")

    def show_recent_searches(self) -> None:
        if not self.recent_searches:
            print("No recent searches.")
            return
        print("\nRecent searches:")
        for idx, query in enumerate(self.recent_searches, 1):
            print(f"[{idx}] {query}")

    def show_favorites(self) -> Optional[str]:
        if not self.favorites:
            print("No favorites yet.")
            return
        all_entries = {item["id"]: item for item in self.kb.get_all()}
        rows = [all_entries[entry_id] for entry_id in self.favorites if entry_id in all_entries]
        if not rows:
            print("Favorites reference old IDs; run new searches to rebuild list.")
            return
        return self.paginated_results(rows)

    def export_cli(self) -> None:
        output = self._input("Export JSON path (default: knowledge_export.json) > ").strip()
        path = Path(output) if output else Path("knowledge_export.json")
        count = self.updater.export_to_json(path)
        print(f"Exported {count} records to {path}.")

    def import_cli(self) -> None:
        source = self._input("Import JSON path > ").strip()
        if not source:
            return
        path = Path(source)
        if not path.exists():
            print("File not found.")
            return
        count = self.updater.import_from_json(path)
        print(f"Imported {count} records from {path}.")

    def read_item(self, item: Dict) -> Optional[str]:
        content = clean_display_text(item["content"])
        pages = self._paginate_article(content, lines_per_page=22)
        page_index = 0

        while True:
            print(f"\n{item['title']}")
            print(f"Category: {item['category']} | Source: {item['source']}")
            print(f"Article page {page_index + 1}/{len(pages)}")
            print("-" * 60)
            print(pages[page_index])
            print("-" * 60)
            print("[n] next page  [p] previous page  [b] back to results  [menu] main menu  [exit] quit")

            action = self._input("[*] > ").strip().lower()
            if action == "b":
                return
            if action == "menu":
                return "menu"
            if action == "exit":
                return "exit"
            if action == "n" and page_index < len(pages) - 1:
                page_index += 1
                continue
            if action == "p" and page_index > 0:
                page_index -= 1
                continue

    def _load_custom_sources(self) -> List[Dict]:
        payload = load_json(CUSTOM_SOURCES_FILE, default=[])
        if isinstance(payload, dict):
            payload = payload.get("sources", [])
        if not isinstance(payload, list):
            return []

        rows: List[Dict] = []
        for idx, item in enumerate(payload, 1):
            if isinstance(item, str):
                rows.append(
                    {
                        "name": f"Custom Source {idx}",
                        "url": item,
                        "categories": [],
                        "queries": [],
                        "enabled": True,
                    }
                )
                continue
            if not isinstance(item, dict):
                continue
            url = str(item.get("url", "")).strip()
            if not url:
                continue
            categories = item.get("categories", [])
            if isinstance(categories, str):
                categories = [categories]
            queries = item.get("queries", [])
            if isinstance(queries, str):
                queries = [queries]
            rows.append(
                {
                    "name": str(item.get("name", "")).strip() or f"Custom Source {idx}",
                    "url": url,
                    "provider": str(item.get("provider", "")).strip().lower() or "generic",
                    "categories": [value for value in categories if value in CATEGORIES],
                    "queries": [str(value).strip() for value in queries if str(value).strip()],
                    "subjects": [str(value).strip() for value in item.get("subjects", []) if str(value).strip()]
                    if isinstance(item.get("subjects", []), list)
                    else ([str(item.get("subjects", "")).strip()] if str(item.get("subjects", "")).strip() else []),
                    "enabled": bool(item.get("enabled", True)),
                }
            )
        return rows

    def _save_custom_sources(self, rows: List[Dict]) -> None:
        save_json(CUSTOM_SOURCES_FILE, rows)

    def manage_custom_sources_cli(self) -> None:
        while True:
            rows = self._load_custom_sources()
            print(f"\nCustom sources configured: {len(rows)}")
            print("[1] List sources")
            print("[2] Add source")
            print("[3] Remove source")
            print("[4] Toggle source enabled")
            print("[5] Edit source")
            print("[0] Back")
            choice = self._input("[*] > ").strip().lower()

            if choice == "0":
                return

            if choice == "1":
                if not rows:
                    print("No custom sources configured.")
                    continue
                for idx, row in enumerate(rows, 1):
                    categories = ", ".join(row.get("categories", [])) or "all"
                    queries = ", ".join(row.get("queries", [])) or "all"
                    subjects = ", ".join(row.get("subjects", [])) or "none"
                    provider = row.get("provider", "generic")
                    enabled = "enabled" if row.get("enabled", True) else "disabled"
                    print(f"[{idx}] {row['name']} ({enabled})")
                    print(f"     Provider: {provider}")
                    print(f"     URL: {row['url']}")
                    print(f"     Categories: {categories}")
                    print(f"     Query filters: {queries}")
                    print(f"     Subjects: {subjects}")
                continue

            if choice == "2":
                name = self._input("Source name > ").strip()
                provider = self._input("Provider [generic/openlibrary] (default: generic) > ").strip().lower()
                if provider not in {"", "generic", "openlibrary"}:
                    provider = "generic"
                url = self._input("Source URL or path (https://..., C:/..., folder, .zip) > ").strip()
                if not url:
                    print("URL is required.")
                    continue
                raw_categories = self._input("Category keys (comma, blank=all) > ").strip()
                raw_queries = self._input("Query filters (comma, blank=all) > ").strip()
                raw_subjects = self._input("OpenLibrary subjects (comma, blank=none) > ").strip()

                categories: List[str] = []
                if raw_categories:
                    categories = [value.strip() for value in raw_categories.split(",") if value.strip()]
                    invalid = [value for value in categories if value not in CATEGORIES]
                    if invalid:
                        print(f"Skipping invalid categories: {', '.join(invalid)}")
                    categories = [value for value in categories if value in CATEGORIES]

                queries = [value.strip() for value in raw_queries.split(",") if value.strip()]
                subjects = [value.strip() for value in raw_subjects.split(",") if value.strip()]
                rows.append(
                    {
                        "name": name or f"Custom Source {len(rows) + 1}",
                        "url": url,
                        "provider": provider or "generic",
                        "categories": categories,
                        "queries": queries,
                        "subjects": subjects,
                        "enabled": True,
                    }
                )
                self._save_custom_sources(rows)
                print("Custom source saved.")
                continue

            if choice == "3":
                if not rows:
                    print("No custom sources to remove.")
                    continue
                target = self._input("Source number to remove > ").strip()
                if not target.isdigit():
                    continue
                idx = int(target) - 1
                if not (0 <= idx < len(rows)):
                    continue
                removed = rows.pop(idx)
                self._save_custom_sources(rows)
                print(f"Removed custom source: {removed['name']}")
                continue

            if choice == "4":
                if not rows:
                    print("No custom sources to toggle.")
                    continue
                target = self._input("Source number to toggle > ").strip()
                if not target.isdigit():
                    continue
                idx = int(target) - 1
                if not (0 <= idx < len(rows)):
                    continue
                rows[idx]["enabled"] = not bool(rows[idx].get("enabled", True))
                self._save_custom_sources(rows)
                state = "enabled" if rows[idx]["enabled"] else "disabled"
                print(f"{rows[idx]['name']} is now {state}.")
                continue

            if choice == "5":
                if not rows:
                    print("No custom sources to edit.")
                    continue
                target = self._input("Source number to edit > ").strip()
                if not target.isdigit():
                    continue
                idx = int(target) - 1
                if not (0 <= idx < len(rows)):
                    continue

                row = rows[idx]
                name = self._input(f"Source name [{row['name']}] > ").strip()
                provider = self._input(
                    f"Provider [{row.get('provider', 'generic')}] (generic/openlibrary) > "
                ).strip().lower()
                url = self._input(f"Source URL or path [{row['url']}] > ").strip()
                raw_categories = self._input("Category keys (comma, blank=keep, *=all) > ").strip()
                raw_queries = self._input("Query filters (comma, blank=keep, *=all) > ").strip()
                raw_subjects = self._input("OpenLibrary subjects (comma, blank=keep, *=none) > ").strip()

                if name:
                    row["name"] = name
                if provider in {"generic", "openlibrary"}:
                    row["provider"] = provider
                if url:
                    row["url"] = url

                if raw_categories:
                    if raw_categories == "*":
                        row["categories"] = []
                    else:
                        categories = [value.strip() for value in raw_categories.split(",") if value.strip()]
                        invalid = [value for value in categories if value not in CATEGORIES]
                        if invalid:
                            print(f"Skipping invalid categories: {', '.join(invalid)}")
                        row["categories"] = [value for value in categories if value in CATEGORIES]

                if raw_queries:
                    if raw_queries == "*":
                        row["queries"] = []
                    else:
                        row["queries"] = [value.strip() for value in raw_queries.split(",") if value.strip()]

                if raw_subjects:
                    if raw_subjects == "*":
                        row["subjects"] = []
                    else:
                        row["subjects"] = [value.strip() for value in raw_subjects.split(",") if value.strip()]

                self._save_custom_sources(rows)
                print(f"Updated custom source: {row['name']}")
                continue

            print("Invalid choice.")

    def _paginate_article(self, content: str, lines_per_page: int = 22) -> List[str]:
        lines = content.splitlines() if content else [""]
        if not lines:
            lines = [""]
        pages: List[str] = []
        for start in range(0, len(lines), lines_per_page):
            pages.append("\n".join(lines[start : start + lines_per_page]))
        return pages or [content]
