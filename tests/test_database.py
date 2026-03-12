from pathlib import Path
from datetime import datetime, timezone

from database import KnowledgeBase


def test_db_init_and_insert(tmp_path: Path) -> None:
    kb = KnowledgeBase(tmp_path / "knowledge.db")
    assert kb.add_knowledge("survival_techniques", "Fire Basics", "Use dry tinder.", "test") is True
    rows = kb.search("fire")
    assert rows
    assert rows[0]["title"] == "Fire Basics"

    entries = kb.get_all()
    assert entries
    timestamp = entries[0]["last_updated"]
    assert isinstance(timestamp, datetime)
    assert timestamp.tzinfo is not None
    assert timestamp.utcoffset() == timezone.utc.utcoffset(timestamp)

    kb.close()


def test_dedup_and_versioning(tmp_path: Path) -> None:
    kb = KnowledgeBase(tmp_path / "knowledge.db")
    assert kb.add_knowledge("trapping", "Snare 101", "Build a loop snare.", "test")
    assert kb.add_knowledge("trapping", "Snare 101", "Build a loop snare.", "test") is False
    assert kb.add_knowledge("trapping", "Snare 101", "Build a spring snare.", "test")
    versions = kb.list_versions("Snare 101")
    assert len(versions) == 1
    changed_at = versions[0]["changed_at"]
    assert isinstance(changed_at, datetime)
    assert changed_at.tzinfo is not None
    assert changed_at.utcoffset() == timezone.utc.utcoffset(changed_at)
    kb.close()


def test_synonym_search(tmp_path: Path) -> None:
    kb = KnowledgeBase(tmp_path / "knowledge.db")
    kb.add_knowledge("trapping", "Snare Construction", "A snare can catch small game.", "test")
    result = kb.search("trap")
    assert result
    kb.close()


def test_search_handles_punctuation_and_fallback(tmp_path: Path) -> None:
    kb = KnowledgeBase(tmp_path / "knowledge.db")
    kb.add_knowledge(
        "survival_techniques",
        "Fire Basics",
        "How to start a fire in wet conditions with tinder and kindling.",
        "test",
    )
    punctuation_query = kb.search("fire???!!")
    assert punctuation_query

    stopword_heavy = kb.search("how do I start a fire")
    assert stopword_heavy
    assert stopword_heavy[0]["title"] == "Fire Basics"
    kb.close()
