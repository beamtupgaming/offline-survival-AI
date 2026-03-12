from __future__ import annotations

import importlib.util
import json
import re
import urllib.parse
import urllib.request
import zipfile
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, Iterable, List, Protocol
from xml.etree import ElementTree

from config import CATEGORIES, CUSTOM_SOURCES_FILE, MEDIA_TYPES
from utils import clean_html, load_json, sanitize_content


class SourcePlugin(Protocol):
    name: str

    def fetch(self, query: str, category: str) -> List[Dict[str, str]]:
        ...


class GutenbergPlugin:
    name = "project_gutenberg"

    def fetch(self, query: str, category: str) -> List[Dict[str, str]]:
        search_url = f"https://gutendex.com/books/?search={urllib.parse.quote(query)}"
        req = urllib.request.Request(
            search_url,
            headers={"User-Agent": "Mozilla/5.0", "Accept-Language": "en-US,en;q=0.9"},
        )
        with urllib.request.urlopen(req, timeout=12) as response:
            if response.status != 200:
                return []
            data = json.loads(response.read().decode("utf-8", errors="ignore"))

        results: List[Dict[str, str]] = []
        for book in data.get("results", [])[:2]:
            title = book.get("title", "Untitled")
            authors = ", ".join(author.get("name", "Unknown") for author in book.get("authors", []))
            text_url = (
                book.get("formats", {}).get("text/html")
                or book.get("formats", {}).get("text/plain")
                or ""
            )
            subjects = ", ".join(book.get("subjects", [])[:6])
            content = f"{title} by {authors}\nSubjects: {subjects}\nRead: {text_url}"
            results.append(
                {
                    "category": category,
                    "title": f"{query.title()} (Gutenberg: {title})",
                    "content": sanitize_content(content),
                    "source": self.name,
                }
            )
        return results


class WikipediaPlugin:
    name = "wikipedia_api"

    def fetch(self, query: str, category: str) -> List[Dict[str, str]]:
        topic = urllib.parse.quote(query.replace(" ", "_"))
        api_url = (
            "https://en.wikipedia.org/w/api.php?action=query&prop=extracts"
            f"&explaintext&format=json&titles={topic}"
        )
        req = urllib.request.Request(
            api_url,
            headers={"User-Agent": "Mozilla/5.0", "Accept-Language": "en-US,en;q=0.9"},
        )
        with urllib.request.urlopen(req, timeout=12) as response:
            if response.status != 200:
                return []
            data = json.loads(response.read().decode("utf-8", errors="ignore"))

        pages = data.get("query", {}).get("pages", {})
        found: List[Dict[str, str]] = []
        for page in pages.values():
            title = page.get("title", query.title())
            extract = sanitize_content(page.get("extract", ""))
            if len(extract) < 250:
                continue
            found.append(
                {
                    "category": category,
                    "title": f"{title} -- Wikipedia",
                    "content": extract,
                    "source": self.name,
                }
            )
            break
        return found


class OfflineMediaPlugin:
    name = "offline_media_index"

    def fetch(self, query: str, category: str) -> List[Dict[str, str]]:
        query_lower = query.lower()
        records: List[Dict[str, str]] = []
        for media_type, directory in MEDIA_TYPES.items():
            if not directory.exists():
                continue
            for file_path in directory.glob("**/*"):
                if not file_path.is_file():
                    continue
                if query_lower and query_lower not in file_path.name.lower():
                    continue
                content = clean_html(f"Offline {media_type} file: {file_path.name}\nPath: {file_path}")
                records.append(
                    {
                        "category": category,
                        "title": f"Offline Media: {file_path.stem}",
                        "content": content,
                        "source": self.name,
                    }
                )
                if len(records) >= 3:
                    return records
        return records


class CustomSourcesPlugin:
    name = "custom_sources"
    SUPPORTED_SUFFIXES = {".txt", ".md", ".html", ".htm", ".pdf", ".doc", ".docx", ".zip"}

    def __init__(self, sources_file: Path) -> None:
        self.sources_file = sources_file
        self._content_cache: Dict[str, str] = {}

    def _normalize_sources(self) -> List[Dict[str, Any]]:
        payload = load_json(self.sources_file, default=[])
        if isinstance(payload, dict):
            payload = payload.get("sources", [])
        if not isinstance(payload, list):
            return []

        normalized: List[Dict[str, Any]] = []
        for idx, item in enumerate(payload, 1):
            if isinstance(item, str):
                url = item.strip()
                if not url:
                    continue
                normalized.append(
                    {
                        "name": f"Custom Source {idx}",
                        "url": url,
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
            categories = [value for value in categories if value in CATEGORIES]

            queries = item.get("queries", [])
            if isinstance(queries, str):
                queries = [queries]
            query_terms = [str(value).strip().lower() for value in queries if str(value).strip()]

            name = str(item.get("name", "")).strip() or f"Custom Source {idx}"
            provider = str(item.get("provider", "")).strip().lower()
            if not provider:
                try:
                    parsed = urllib.parse.urlparse(url)
                    hostname = parsed.hostname or ""
                    if hostname.lower() == "openlibrary.org":
                        provider = "openlibrary"
                except (ValueError, TypeError):
                    pass
            if provider not in {"", "generic", "openlibrary"}:
                provider = "generic"

            subjects = item.get("subjects", [])
            if isinstance(subjects, str):
                subjects = [subjects]
            subject_terms = [str(value).strip().lower() for value in subjects if str(value).strip()]

            normalized.append(
                {
                    "name": name,
                    "url": url,
                    "provider": provider or "generic",
                    "categories": categories,
                    "queries": query_terms,
                    "subjects": subject_terms,
                    "enabled": bool(item.get("enabled", True)),
                }
            )
        return normalized

    def _matches(self, source: Dict[str, Any], query: str, category: str) -> bool:
        if not source.get("enabled", True):
            return False

        categories = source.get("categories", [])
        if categories and category not in categories:
            return False

        query_terms = source.get("queries", [])
        if not query_terms:
            return True

        query_lower = query.lower()
        return any(term in query_lower for term in query_terms)

    def _fetch_source_content(self, url: str) -> str:
        if url in self._content_cache:
            return self._content_cache[url]

        source_type, source_value = self._resolve_source(url)
        if source_type == "file":
            extracted = self._extract_local_source(source_value)
            joined = "\n\n".join(item["content"] for item in extracted)
            self._content_cache[url] = joined
            return joined

        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "Mozilla/5.0", "Accept-Language": "en-US,en;q=0.9"},
            )
            with urllib.request.urlopen(req, timeout=12) as response:
                status = getattr(response, "status", 200)
                if isinstance(status, int) and status >= 400:
                    self._content_cache[url] = ""
                    return ""
                text = response.read().decode("utf-8", errors="ignore")
        except Exception:
            self._content_cache[url] = ""
            return ""

        cleaned = sanitize_content(clean_html(text))
        self._content_cache[url] = cleaned
        return cleaned

    def _resolve_source(self, value: str) -> tuple[str, Path | str]:
        stripped = value.strip()
        parsed = urllib.parse.urlparse(stripped)
        if len(parsed.scheme) == 1 and len(stripped) >= 3 and stripped[1] == ":" and stripped[2] in {"\\", "/"}:
            return "file", Path(stripped).expanduser()
        if parsed.scheme in {"http", "https"}:
            return "url", stripped
        if parsed.scheme == "file":
            return "file", Path(urllib.request.url2pathname(parsed.path))
        if parsed.scheme:
            return "url", stripped
        return "file", Path(stripped).expanduser()

    def _extract_local_source(self, path: Path) -> List[Dict[str, str]]:
        if not path.exists():
            return []
        if path.is_dir():
            rows: List[Dict[str, str]] = []
            for file_path in sorted(path.rglob("*")):
                if not file_path.is_file() or file_path.suffix.lower() not in self.SUPPORTED_SUFFIXES:
                    continue
                rows.extend(self._extract_file_path(file_path))
            return rows
        return self._extract_file_path(path)

    def _extract_file_path(self, path: Path) -> List[Dict[str, str]]:
        suffix = path.suffix.lower()
        if suffix not in self.SUPPORTED_SUFFIXES:
            return []
        try:
            data = path.read_bytes()
        except Exception:
            return []
        return self._extract_from_bytes(data, path.name, str(path))

    def _extract_from_bytes(self, data: bytes, file_name: str, origin: str) -> List[Dict[str, str]]:
        suffix = Path(file_name).suffix.lower()

        if suffix == ".zip":
            rows: List[Dict[str, str]] = []
            try:
                with zipfile.ZipFile(BytesIO(data)) as archive:
                    for member in archive.infolist():
                        if member.is_dir():
                            continue
                        inner_name = member.filename
                        inner_suffix = Path(inner_name).suffix.lower()
                        if inner_suffix not in self.SUPPORTED_SUFFIXES or inner_suffix == ".zip":
                            continue
                        try:
                            inner_data = archive.read(member)
                        except Exception:
                            continue
                        rows.extend(self._extract_from_bytes(inner_data, inner_name, f"{origin}::{inner_name}"))
            except Exception:
                return []
            return rows

        text = self._extract_text(data, file_name)
        if len(text) < 80:
            return []
        return [
            {
                "title": Path(file_name).stem,
                "content": f"Source file: {origin}\n\n{text}",
            }
        ]

    def _extract_text(self, data: bytes, file_name: str) -> str:
        suffix = Path(file_name).suffix.lower()
        if suffix in {".txt", ".md", ".html", ".htm"}:
            decoded = data.decode("utf-8", errors="ignore")
            return sanitize_content(clean_html(decoded))
        if suffix == ".pdf":
            return self._extract_pdf_text_from_bytes(data)
        if suffix == ".docx":
            return self._extract_docx_text_from_bytes(data)
        if suffix == ".doc":
            return self._extract_doc_text_from_bytes(data)
        return ""

    def _extract_pdf_text_from_bytes(self, data: bytes) -> str:
        try:
            from pypdf import PdfReader
        except Exception:
            return ""
        try:
            reader = PdfReader(BytesIO(data))
            chunks = [(page.extract_text() or "") for page in reader.pages]
            return sanitize_content("\n".join(chunks))
        except Exception:
            return ""

    def _extract_docx_text_from_bytes(self, data: bytes) -> str:
        try:
            with zipfile.ZipFile(BytesIO(data)) as archive:
                xml_bytes = archive.read("word/document.xml")
        except Exception:
            return ""
        try:
            root = ElementTree.fromstring(xml_bytes)
        except Exception:
            return ""

        ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
        lines: List[str] = []
        for paragraph in root.iter(f"{ns}p"):
            parts = [node.text for node in paragraph.iter(f"{ns}t") if node.text]
            text = "".join(parts).strip()
            if text:
                lines.append(text)
        return sanitize_content("\n".join(lines))

    def _extract_doc_text_from_bytes(self, data: bytes) -> str:
        try:
            utf16_matches = re.findall(rb"(?:[\x20-\x7E]\x00){8,}", data)
            utf16_chunks = [chunk.decode("utf-16le", errors="ignore") for chunk in utf16_matches]
            ascii_matches = re.findall(rb"[\x20-\x7E]{16,}", data)
            ascii_chunks = [chunk.decode("latin1", errors="ignore") for chunk in ascii_matches]
            combined = "\n".join(utf16_chunks + ascii_chunks)
            cleaned = re.sub(r"\s+", " ", combined).strip()
            return sanitize_content(cleaned)
        except Exception:
            return ""

    def _infer_category(self, subjects: List[str], fallback_category: str) -> str:
        joined = " ".join(subjects).lower()
        keyword_map: Dict[str, str] = {
            "fish": "fishing",
            "angling": "fishing",
            "trap": "trapping",
            "snare": "trapping",
            "hunt": "hunting",
            "wound": "first_aid_basic",
            "trauma": "first_aid_advanced",
            "medical": "first_aid_basic",
            "first aid": "first_aid_basic",
            "shelter": "survival_techniques",
            "bushcraft": "survival_techniques",
            "water": "survival_techniques",
            "survival": "survival_techniques",
            "plant": "plant_growing",
            "gardening": "plant_growing",
            "build": "building_methods",
            "architecture": "building_methods",
            "skinning": "skinning",
            "hide": "skinning",
            "wilderness": "survival_books",
            "manual": "survival_books",
        }
        for keyword, mapped in keyword_map.items():
            if keyword in joined:
                return mapped
        return fallback_category if fallback_category in CATEGORIES else "survival_techniques"

    def _fetch_openlibrary_subject(self, subject: str) -> List[Dict[str, Any]]:
        slug = urllib.parse.quote(subject.strip().replace(" ", "_").lower())
        if not slug:
            return []
        url = f"https://openlibrary.org/subjects/{slug}.json?limit=12"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept-Language": "en-US,en;q=0.9"})
        try:
            with urllib.request.urlopen(req, timeout=12) as response:
                if getattr(response, "status", 200) != 200:
                    return []
                payload = json.loads(response.read().decode("utf-8", errors="ignore"))
        except Exception:
            return []
        return payload.get("works", []) if isinstance(payload, dict) else []

    def _build_openlibrary_documents(self, source: Dict[str, Any], fallback_category: str) -> List[Dict[str, str]]:
        subjects = source.get("subjects", [])
        if not subjects:
            subjects = [term for term in source.get("queries", []) if term]
        if not subjects:
            subjects = ["survival"]

        docs: List[Dict[str, str]] = []
        seen_titles: set[str] = set()
        for subject in subjects[:6]:
            for work in self._fetch_openlibrary_subject(subject):
                title = str(work.get("title", "")).strip()
                if not title or title in seen_titles:
                    continue
                seen_titles.add(title)

                work_subjects = [str(item).strip() for item in work.get("subject", []) if str(item).strip()]
                author_names = [str(author.get("name", "")).strip() for author in work.get("authors", []) if str(author.get("name", "")).strip()]

                first_sentence = work.get("first_sentence", "")
                if isinstance(first_sentence, dict):
                    first_sentence = first_sentence.get("value", "")
                summary = str(first_sentence).strip()

                publish_year = work.get("first_publish_year", "Unknown")
                key = str(work.get("key", "")).strip()
                openlibrary_url = f"https://openlibrary.org{key}" if key.startswith("/") else source["url"]

                inferred_category = self._infer_category(work_subjects + source.get("subjects", []), fallback_category)
                if source.get("categories") and inferred_category not in source.get("categories", []):
                    inferred_category = source.get("categories", [fallback_category])[0]

                content_parts = [
                    f"Title: {title}",
                    f"Authors: {', '.join(author_names) if author_names else 'Unknown'}",
                    f"First published: {publish_year}",
                    f"OpenLibrary page: {openlibrary_url}",
                    f"Seed subject: {subject}",
                ]
                if work_subjects:
                    content_parts.append(f"Subjects: {', '.join(work_subjects[:20])}")
                if summary:
                    content_parts.append(f"Summary: {summary}")

                docs.append(
                    {
                        "category": inferred_category,
                        "title": f"{source['name']} :: {title} -- OpenLibrary",
                        "content": sanitize_content("\n".join(content_parts)),
                        "source": f"custom_source:{source['name']}:openlibrary",
                    }
                )
        return docs

    def fetch(self, query: str, category: str) -> List[Dict[str, str]]:
        found: List[Dict[str, str]] = []
        for source in self._normalize_sources():
            if not self._matches(source, query, category):
                continue

            if source.get("provider") == "openlibrary":
                docs = self._build_openlibrary_documents(source, category)
                query_lower = query.lower()
                for row in docs:
                    searchable = f"{row['title']}\n{row['content']}".lower()
                    if source.get("queries") and not any(term in query_lower or term in searchable for term in source["queries"]):
                        continue
                    if row["category"] != category:
                        continue
                    found.append(row)
                continue

            source_type, source_value = self._resolve_source(source["url"])
            if source_type == "file":
                for item in self._extract_local_source(source_value):
                    found.append(
                        {
                            "category": category,
                            "title": f"{source['name']} :: {item['title']} -- Custom Source",
                            "content": item["content"],
                            "source": f"custom_source:{source['name']}",
                        }
                    )
                continue

            content = self._fetch_source_content(source["url"])
            if len(content) < 80:
                continue
            found.append(
                {
                    "category": category,
                    "title": f"{source['name']} -- Custom Source",
                    "content": f"Source URL: {source['url']}\n\n{content}",
                    "source": f"custom_source:{source['name']}",
                }
            )
        return found


class PluginManager:
    def __init__(self, plugin_dir: Path, custom_sources_file: Path = CUSTOM_SOURCES_FILE) -> None:
        self.plugin_dir = plugin_dir
        self.plugins: List[SourcePlugin] = [
            GutenbergPlugin(),
            WikipediaPlugin(),
            OfflineMediaPlugin(),
            CustomSourcesPlugin(custom_sources_file),
        ]
        self._load_external_plugins()

    def _load_external_plugins(self) -> None:
        if not self.plugin_dir.exists():
            return
        for py_file in self.plugin_dir.glob("*.py"):
            module_name = f"user_plugin_{py_file.stem}"
            spec = importlib.util.spec_from_file_location(module_name, py_file)
            if spec is None or spec.loader is None:
                continue
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            plugin_cls = getattr(module, "Plugin", None)
            if plugin_cls is None:
                continue
            plugin = plugin_cls()
            if hasattr(plugin, "fetch") and hasattr(plugin, "name"):
                self.plugins.append(plugin)

    def fetch_all(self, queries: Iterable[str], category: str) -> List[Dict[str, str]]:
        if category not in CATEGORIES:
            return []
        collected: List[Dict[str, str]] = []
        for query in queries:
            for plugin in self.plugins:
                try:
                    collected.extend(plugin.fetch(query, category))
                except Exception:
                    continue
        return collected
