import json
from pathlib import Path

from database import KnowledgeBase
from plugins import PluginManager
from updater import ContentUpdater, FileCache


class _FakeResponse:
    def __init__(self, payload: dict, status: int = 200) -> None:
        self._payload = payload
        self.status = status

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return None


def test_openlibrary_subject_source_ingests_individual_topics(tmp_path: Path, monkeypatch) -> None:
    import plugins as plugins_module

    def fake_urlopen(request, timeout=12):
        _ = timeout
        url = request.full_url
        if "openlibrary.org/subjects" in url:
            return _FakeResponse(
                {
                    "works": [
                        {
                            "title": "Primitive Fishing Skills",
                            "authors": [{"name": "R. Scout"}],
                            "first_publish_year": 1988,
                            "first_sentence": {"value": "Use line, hook, and natural bait from local habitat."},
                            "subject": ["Fishing", "Survival", "Wilderness"],
                            "key": "/works/OL1W",
                        },
                        {
                            "title": "Improvised River Traps",
                            "authors": [{"name": "K. Woods"}],
                            "first_publish_year": 1992,
                            "first_sentence": "Simple funnel traps can be built from branches and cordage.",
                            "subject": ["Fishing", "Trapping"],
                            "key": "/works/OL2W",
                        },
                    ]
                }
            )
        raise AssertionError(f"Unexpected URL: {url}")

    monkeypatch.setattr(plugins_module.urllib.request, "urlopen", fake_urlopen)

    custom_sources_path = tmp_path / "custom_sources.json"
    custom_sources_path.write_text(
        json.dumps(
            [
                {
                    "name": "OpenLibrary Survival",
                    "provider": "openlibrary",
                    "url": "https://openlibrary.org",
                    "subjects": ["fishing"],
                    "categories": ["fishing"],
                    "queries": ["fishing", "improvised"],
                    "enabled": True,
                }
            ]
        ),
        encoding="utf-8",
    )

    kb = KnowledgeBase(tmp_path / "knowledge.db")
    cache = FileCache(tmp_path / "cache")
    manager = PluginManager(tmp_path / "plugins", custom_sources_file=custom_sources_path)
    manager.plugins = [plugin for plugin in manager.plugins if plugin.name == "custom_sources"]
    updater = ContentUpdater(kb, cache, manager)

    added = updater._fetch_public_data()
    assert added >= 1

    rows = kb.search("rudimentary fishing setup")
    assert rows
    assert any("OpenLibrary Survival :: Primitive Fishing Skills" in row["title"] for row in rows)
    assert any(row["category"] == "fishing" for row in rows)
    kb.close()
