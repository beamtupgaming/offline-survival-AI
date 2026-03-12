import json
from pathlib import Path

import cli as cli_module
from cli import CLI
from database import KnowledgeBase


class DummyUpdater:
    def __init__(self, scraper) -> None:
        self.scraper = scraper
        self.called = False

    def auto_update(self) -> int:
        self.called = True
        assert self.scraper.active, "scraper should remain paused as active user during manual update"
        return 0


class DummyScraper:
    def __init__(self) -> None:
        self.active = False
        self.events: list[bool] = []

    def set_user_active(self, active: bool) -> None:
        self.active = active
        self.events.append(active)


class CliHarness(CLI):
    def __init__(self, kb, updater, scraper, inputs: list[str]) -> None:
        super().__init__(kb, updater, scraper)
        self._inputs = inputs

    def _input(self, label: str) -> str:
        return self._inputs.pop(0)


def test_manual_update_runs_while_user_marked_active(tmp_path: Path) -> None:
    kb = KnowledgeBase(tmp_path / "knowledge.db")
    scraper = DummyScraper()
    updater = DummyUpdater(scraper)
    cli = CliHarness(kb, updater, scraper, ["5", "0"])

    cli.run()

    assert updater.called
    assert scraper.events[:2] == [True, False]
    assert scraper.events[-1] is False
    kb.close()


def test_manage_custom_sources_edit_updates_saved_entry(tmp_path: Path, monkeypatch) -> None:
    custom_sources_file = tmp_path / "custom_sources.json"
    custom_sources_file.write_text(
        json.dumps(
            [
                {
                    "name": "Old Source",
                    "url": "https://example.com/old",
                    "categories": ["survival_techniques"],
                    "queries": ["shelter"],
                    "enabled": True,
                }
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(cli_module, "CUSTOM_SOURCES_FILE", custom_sources_file)

    kb = KnowledgeBase(tmp_path / "knowledge.db")
    scraper = DummyScraper()
    updater = DummyUpdater(scraper)
    cli = CliHarness(
        kb,
        updater,
        scraper,
        [
            "5",
            "1",
            "Field Notes",
            "",
            "https://example.com/new",
            "first_aid_basic,invalid_key",
            "water,fire",
            "",
            "0",
        ],
    )

    cli.manage_custom_sources_cli()

    payload = json.loads(custom_sources_file.read_text(encoding="utf-8"))
    assert len(payload) == 1
    assert payload[0]["name"] == "Field Notes"
    assert payload[0]["url"] == "https://example.com/new"
    assert payload[0]["categories"] == ["first_aid_basic"]
    assert payload[0]["queries"] == ["water", "fire"]
    kb.close()


def test_generate_answer_uses_relevant_sentences(tmp_path: Path) -> None:
    kb = KnowledgeBase(tmp_path / "knowledge.db")
    kb.add_knowledge(
        "fishing",
        "Improvised Fishing Setup",
        (
            "Start by finding a flexible branch and tying cordage to create a line. "
            "Carve a small hook from bone, thorn, or bent pin and secure it tightly. "
            "Use insects or grubs as bait and place the line near shaded moving water. "
            "Keep a second line ready so you can rotate spots and improve catch rate."
        ),
        source="test",
    )
    cli = CliHarness(kb, DummyUpdater(DummyScraper()), DummyScraper(), [])

    results = kb.search("teach me how to make a rudimentary fishing setup using found materials", limit=20)
    answer = cli._generate_answer("teach me how to make a rudimentary fishing setup using found materials", results)

    assert "step-by-step (field manual):" in answer.lower()
    assert "field notes:" in answer.lower()
    assert "1." in answer
    assert "flexible branch" in answer.lower() or "cordage" in answer.lower()
    assert "hook" in answer.lower()
    kb.close()


def test_generate_answer_compact_style(tmp_path: Path) -> None:
    kb = KnowledgeBase(tmp_path / "knowledge.db")
    kb.add_knowledge(
        "fishing",
        "Compact Fishing Setup",
        (
            "Use a flexible branch as a rod and tie cordage as line. "
            "Shape a small hook from available metal, thorn, or bone. "
            "Set bait from local insects and place in shaded moving water."
        ),
        source="test",
    )
    cli = CliHarness(kb, DummyUpdater(DummyScraper()), DummyScraper(), [])
    cli.chat_style = "compact"

    results = kb.search("rudimentary fishing setup", limit=20)
    answer = cli._generate_answer("rudimentary fishing setup", results)

    assert "step-by-step (field manual):" not in answer.lower()
    assert "field notes:" not in answer.lower()
    assert "cordage" in answer.lower() or "flexible branch" in answer.lower()
    kb.close()
