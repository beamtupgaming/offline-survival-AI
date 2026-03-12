import json
import zipfile
from io import BytesIO
from pathlib import Path

from database import KnowledgeBase
from plugins import PluginManager
from updater import ContentUpdater, FileCache


def _build_minimal_docx_bytes(paragraphs: list[str]) -> bytes:
    body = "".join(
        f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p>" for text in paragraphs
    )
    xml = (
        "<?xml version='1.0' encoding='UTF-8' standalone='yes'?>"
        "<w:document xmlns:w='http://schemas.openxmlformats.org/wordprocessingml/2006/main'>"
        f"<w:body>{body}</w:body>"
        "</w:document>"
    ).encode("utf-8")

    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", "<Types xmlns='http://schemas.openxmlformats.org/package/2006/content-types'></Types>")
        archive.writestr("word/document.xml", xml)
    return buf.getvalue()


def _build_doc_like_bytes(text: str) -> bytes:
    return ("HEADER".encode("ascii") + text.encode("utf-16le") + b"\x00\x00FOOTER")


def test_custom_source_zip_with_docx_and_doc_is_ingested(tmp_path: Path) -> None:
    bundle_path = tmp_path / "survival_bundle.zip"
    with zipfile.ZipFile(bundle_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "manual.docx",
            _build_minimal_docx_bytes(
                [
                    "Prepare insulation and roof drainage before storm season.",
                    "Sort fuel by size and keep a dry tinder reserve.",
                    "Practice route planning and fallback shelter locations.",
                ]
            ),
        )
        archive.writestr(
            "legacy.doc",
            _build_doc_like_bytes(
                "Legacy field notes discuss emergency signaling mirrors and fire lay patterns for wet climates.",
            ),
        )

    custom_sources_path = tmp_path / "custom_sources.json"
    custom_sources_path.write_text(
        json.dumps(
            [
                {
                    "name": "Bundle Source",
                    "url": str(bundle_path),
                    "categories": ["survival_techniques"],
                    "queries": ["shelter", "fire"],
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

    rows = kb.search("insulation drainage")
    assert rows
    assert any("Bundle Source :: manual" in row["title"] for row in rows)

    legacy_rows = kb.search("legacy signaling mirrors")
    assert legacy_rows
    assert any("Bundle Source :: legacy" in row["title"] for row in legacy_rows)
    kb.close()
