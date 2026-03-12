import json
from pathlib import Path

from database import KnowledgeBase
from plugins import PluginManager
from updater import ContentUpdater, FileCache


def test_custom_source_is_ingested_by_updater(tmp_path: Path) -> None:
    article_path = tmp_path / "field_notes.html"
    article_path.write_text(
        "<h1>Shelter Notes</h1><p>Build insulation first, then weatherproof roof and drainage.</p>"
        "<p>Use dry tinder preparation before dark and keep fuel sorted by size.</p>",
        encoding="utf-8",
    )

    custom_sources_path = tmp_path / "custom_sources.json"
    custom_sources_path.write_text(
        json.dumps(
            [
                {
                    "name": "Field Notes",
                    "url": article_path.as_uri(),
                    "categories": ["survival_techniques"],
                    "queries": ["shelter"],
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

    records = kb.search("insulation")
    assert records
    assert any(str(row.get("source", "")).startswith("custom_source:") for row in records)
    kb.close()
