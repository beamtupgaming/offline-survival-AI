from __future__ import annotations

from pathlib import Path
from typing import Dict

APP_DIR: Path = Path.home() / ".survival_chatbot"
DB_PATH: Path = APP_DIR / "knowledge.db"
CACHE_DIR: Path = APP_DIR / "cache"
MEDIA_DIR: Path = APP_DIR / "media"
MEDIA_TRACKING_FILE: Path = APP_DIR / "media_tracking.json"
CUSTOM_SOURCES_FILE: Path = APP_DIR / "custom_sources.json"
SCRAPE_TRACKING_FILE: Path = APP_DIR / "scrape_tracking.json"
RECENT_SEARCHES_FILE: Path = APP_DIR / "recent_searches.json"
FAVORITES_FILE: Path = APP_DIR / "favorites.json"

UPDATE_INTERVAL_SECONDS = 1800
BACKGROUND_MIN_INTERVAL_SECONDS = 7200
MAX_CONTENT_LENGTH = 120_000
WATCHDOG_TIMEOUT_SECONDS = 90
LATE_COMPLETION_LOG_COOLDOWN_SECONDS = 300

MEDIA_TYPES: Dict[str, Path] = {
    "videos": MEDIA_DIR / "videos",
    "pdfs": MEDIA_DIR / "pdfs",
    "audio": MEDIA_DIR / "audio",
    "documents": MEDIA_DIR / "documents",
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
    "wikipedia_survival": "Wikipedia Survival Topics",
}

SYNONYMS = {
    "trap": ["snare", "deadfall"],
    "snare": ["trap"],
    "water": ["hydration", "purify", "purification"],
    "shelter": ["lean-to", "debris hut"],
    "fire": ["kindling", "tinder", "ignite"],
    "food": ["forage", "hunting", "fishing"],
}

STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "how",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "that",
    "the",
    "to",
    "what",
    "when",
    "where",
    "which",
    "with",
}


def ensure_app_dirs() -> None:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    for media_path in MEDIA_TYPES.values():
        media_path.mkdir(parents=True, exist_ok=True)
