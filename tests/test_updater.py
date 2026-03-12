import json
from pathlib import Path

from database import KnowledgeBase
from plugins import PluginManager
from updater import ContentUpdater, FileCache


class DummyPlugin:
    name = "dummy"

    def fetch(self, query: str, category: str):
        return [
            {
                "category": category,
                "title": f"{query} title",
                "content": "content",
                "source": self.name,
            }
        ]


def test_import_export(tmp_path: Path) -> None:
    kb = KnowledgeBase(tmp_path / "knowledge.db")
    cache = FileCache(tmp_path / "cache")
    manager = PluginManager(tmp_path / "plugins")
    manager.plugins = [DummyPlugin()]
    updater = ContentUpdater(kb, cache, manager)

    updater._init_builtin()
    export_path = tmp_path / "export.json"
    count = updater.export_to_json(export_path)
    assert count > 0

    payload = json.loads(export_path.read_text(encoding="utf-8"))
    payload.append({"category": "hunting", "title": "Tracking 101", "content": "look for signs"})
    export_path.write_text(json.dumps(payload), encoding="utf-8")

    imported = updater.import_from_json(export_path)
    assert imported >= 1
    kb.close()
