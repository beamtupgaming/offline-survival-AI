from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List

from config import MAX_CONTENT_LENGTH, STOPWORDS, SYNONYMS


def load_json(path: Path, default: Any = None) -> Any:
    if default is None:
        default = {}
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")


def clean_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text)
    entities = {
        "&nbsp;": " ",
        "&quot;": '"',
        "&amp;": "&",
        "&lt;": "<",
        "&gt;": ">",
    }
    for entity, char in entities.items():
        text = text.replace(entity, char)
    return text.strip()


def clean_display_text(text: str) -> str:
    if not isinstance(text, str):
        text = str(text)
    cleaned = "".join(ch for ch in text if ch.isprintable() or ch in "\n\t")
    return cleaned.strip()


def sanitize_content(content: str) -> str:
    stripped = clean_html(content)
    collapsed = re.sub(r"\n{3,}", "\n\n", stripped).strip()
    if len(collapsed) > MAX_CONTENT_LENGTH:
        return collapsed[:MAX_CONTENT_LENGTH]
    return collapsed


def tokenize_query(query: str) -> List[str]:
    tokens = re.split(r"[^a-zA-Z0-9]+", query.lower())
    return [token for token in tokens if token and token not in STOPWORDS]


def expand_with_synonyms(tokens: Iterable[str]) -> List[str]:
    expanded: List[str] = []
    seen = set()
    for token in tokens:
        if token not in seen:
            expanded.append(token)
            seen.add(token)
        for syn in SYNONYMS.get(token, []):
            if syn not in seen:
                expanded.append(syn)
                seen.add(syn)
    return expanded


def safe_title(title: str, max_len: int = 100) -> str:
    return re.sub(r"[<>:\"/\\|?*]", "", title)[:max_len].strip()


def chunked(items: List[Dict[str, Any]], size: int) -> List[List[Dict[str, Any]]]:
    return [items[idx : idx + size] for idx in range(0, len(items), size)]
