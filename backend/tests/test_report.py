"""Unit tests for report.py (blocks 5/6 — GPT narrative reports).

No FastAPI app / DB involved: report.py is decoupled from models.py the same
way a broker service's schemas.py is (see its module docstring), so these
exercise it directly with EntryFacts/FileEvidence built by hand. The LLM call
itself (`report._call_llm`) is monkeypatched rather than hitting a real
Yandex Cloud endpoint or a fake OpenAI client — see conftest.py's own
_fake_publish for the same "mock at the outermost synchronous boundary"
approach used for the broker services.
"""

import uuid
from datetime import date

import report


def _facts() -> tuple[report.EntryFacts, uuid.UUID, uuid.UUID]:
    empty_id = uuid.uuid4()
    equipped_id = uuid.uuid4()
    facts = report.EntryFacts(
        project_name="ЖК «Северный»",
        entry_date=date(2026, 3, 14),
        phase_name="Earthwork",
        phase_confidence=0.8,
        delay_days=12,
        expected_completion=date(2026, 6, 1),
        files=[
            report.FileEvidence(
                asset_id=empty_id,
                name="empty_site.jpg",
                url=f"http://backend/files/{empty_id}",
                kind="image",
                equipment={},
            ),
            report.FileEvidence(
                asset_id=equipped_id,
                name="excavator.jpg",
                url=f"http://backend/files/{equipped_id}",
                kind="image",
                equipment={"excavator": 1},
            ),
        ],
    )
    return facts, empty_id, equipped_id


def test_resolve_citations_replaces_marker_with_real_link():
    facts, empty_id, _ = _facts()
    files_by_id = {str(f.asset_id): f for f in facts.files}
    text = f"На площадке техники не видно.[[file:{empty_id}]] Это объясняет отставание."

    out = report._resolve_citations(text, files_by_id)

    assert f"[empty_site.jpg](http://backend/files/{empty_id})" in out
    assert "[[file:" not in out


def test_resolve_citations_drops_unknown_id_rather_than_link_or_leak_marker():
    # A hallucinated/mismatched id (shouldn't happen given the closed list in
    # the prompt, but the model's raw output isn't trusted) must never turn
    # into a link, and the marker must not leak into the rendered text.
    text = f"Что-то [[file:{uuid.uuid4()}]] непонятное."

    out = report._resolve_citations(text, {})

    assert "[[file:" not in out
    assert "http" not in out


def test_facts_to_prompt_offers_every_file_id_and_delay_framing():
    facts, empty_id, equipped_id = _facts()

    prompt = report._facts_to_prompt(facts)

    assert str(empty_id) in prompt
    assert str(equipped_id) in prompt
    assert "техника не обнаружена" in prompt  # the empty file's own finding
    assert "excavator × 1" in prompt
    assert "отставание 12 дн." in prompt


def test_generate_entry_report_returns_none_without_a_client(monkeypatch):
    # No YANDEX_CLOUD_* configured — see report.py's module-level _client.
    monkeypatch.setattr(report, "_client", None)
    facts, _, _ = _facts()

    assert report.generate_entry_report(facts) is None


def test_generate_entry_report_grounds_citation_in_a_real_link(monkeypatch):
    facts, empty_id, _ = _facts()

    def fake_call_llm(system: str, user: str) -> str:
        assert str(empty_id) in user  # the file's id must be offered to the model
        return f"Техника на площадке отсутствует.[[file:{empty_id}]]"

    monkeypatch.setattr(report, "_client", object())  # truthy stand-in
    monkeypatch.setattr(report, "_call_llm", fake_call_llm)

    out = report.generate_entry_report(facts)

    assert out is not None
    assert f"[empty_site.jpg](http://backend/files/{empty_id})" in out


def test_generate_entry_report_returns_none_on_llm_failure(monkeypatch):
    facts, _, _ = _facts()

    def boom(system: str, user: str) -> str:
        raise RuntimeError("network error")

    monkeypatch.setattr(report, "_client", object())
    monkeypatch.setattr(report, "_call_llm", boom)

    assert report.generate_entry_report(facts) is None


def test_generate_project_report_uses_the_project_prompt(monkeypatch):
    facts, _, _ = _facts()
    seen_system = []

    def fake_call_llm(system: str, user: str) -> str:
        seen_system.append(system)
        return "Готово."

    monkeypatch.setattr(report, "_client", object())
    monkeypatch.setattr(report, "_call_llm", fake_call_llm)

    assert report.generate_project_report(facts) == "Готово."
    assert seen_system == [report._PROJECT_SYSTEM_PROMPT]
