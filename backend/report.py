"""Blocks 5/6 — GPT narrative reports (диаграмма: блоки 5, 6).

Per backend/integrations/README.md, these two blocks stand apart from the
rest of the pipeline: nothing calls them as a service, so — unlike
vision/phase/delay/planner/visual_phase — there's no `*.command`/`*.result`
leg here, no worker container, no RabbitMQ. This module is a plain function
called in-process from analysis.py (`handle_delay_result`/
`_refresh_plan_status`), the same way planner's own
`_estimate_project_duration` is an in-process LLM call inside a broker-based
service. That's not a second integration style bolted onto the pipeline
(see the `consistent-service-integration-pattern` principle) — it's not an
inter-service call at all, since nothing outside this process ever invokes
it.

Block 5 (`generate_entry_report`): a short narrative for one journal entry
(one upload batch), grounded in that entry's phase/delay numbers plus each
of its files' OWN equipment findings — not just the entry's merged totals —
so the model can cite a *specific* photo/video for a specific claim (e.g.
"no equipment is visible in this photo, which is consistent with the
delay"). Block 6 (`generate_project_report`): the same facts, framed as the
project's current top-line status instead of one visit's journal entry —
see analysis.py's `_refresh_plan_status`.

Grounding, not free generation: the model is given a closed list of this
entry's files (id/name/url/kind/equipment) and asked to write prose that
cites one only via a `[[file:ID]]` marker chosen from that list — never
asked to produce a URL itself. `_resolve_citations` is what turns a marker
into a real `[name](url)` markdown link (or drops it, if the model somehow
emits an id that isn't in the list) — the same closed-vocabulary discipline
`services/planner/plan_normalizer.py` uses for activity classification,
applied here to *evidence* instead of activity names, so a citation can
never point at a file that wasn't actually part of this entry.

REAL model: same Yandex Cloud OpenAI-compatible endpoint (YandexGPT) as
`services/planner`, structured (json_schema) output. Optional, unlike
planner: this is a nicety on top of an already-complete pipeline (the
template-rendered `stage_summary`/`equipment_summary` are what the pipeline
actually depends on), so a missing YANDEX_CLOUD_* key or a failed call just
means `generate_*_report` returns None — logged, never raised — rather than
failing the entry the way a bad planner response does.
"""

from __future__ import annotations

import json
import logging
import os
import re
import uuid
from dataclasses import dataclass, field
from datetime import date as date_type

import openai

log = logging.getLogger("csm.report")

YANDEX_CLOUD_FOLDER = os.environ.get("YANDEX_CLOUD_FOLDER")
# Never hardcode this — set it via the environment, same as services/planner.
YANDEX_CLOUD_API_KEY = os.environ.get("YANDEX_CLOUD_API_KEY")
YANDEX_CLOUD_MODEL = os.environ.get("YANDEX_CLOUD_MODEL", "yandexgpt-5-lite/latest")

# None (rather than services/planner's fail-fast) is deliberate — see module
# docstring: narrative generation is optional, so a backend deployment that
# hasn't set these yet just runs without it instead of refusing to start.
_client: openai.OpenAI | None = None
if YANDEX_CLOUD_API_KEY and YANDEX_CLOUD_FOLDER:
    _client = openai.OpenAI(
        api_key=YANDEX_CLOUD_API_KEY,
        base_url="https://ai.api.cloud.yandex.net/v1",
        project=YANDEX_CLOUD_FOLDER,
        default_headers={"x-data-logging-enabled": "false"},
    )
else:
    log.info("YANDEX_CLOUD_FOLDER/YANDEX_CLOUD_API_KEY not set — narrative reports disabled")


@dataclass
class FileEvidence:
    """One journal-entry file's own equipment findings — as opposed to
    EquipmentObservation's day-merged, cross-file totals (see
    MediaAsset.equipment_counts). An empty `equipment` dict is itself a
    fact worth citing: "no equipment visible in this photo" is exactly the
    kind of claim the narrative should ground in a specific file."""

    asset_id: uuid.UUID
    name: str
    url: str
    kind: str  # "image" | "video"
    equipment: dict[str, int] = field(default_factory=dict)


@dataclass
class EntryFacts:
    """Structured inputs for both blocks 5 and 6 — everything the narrative
    is allowed to talk about. See analysis.py's _build_entry_facts for how
    this is assembled from JournalEntry/MediaAsset."""

    project_name: str
    entry_date: date_type
    phase_name: str | None
    phase_confidence: float | None
    delay_days: int | None
    expected_completion: date_type | None
    files: list[FileEvidence]


_CITATION_RE = re.compile(r"\[\[file:([0-9a-fA-F-]{36})\]\]")


def _resolve_citations(text: str, files_by_id: dict[str, FileEvidence]) -> str:
    """Turns each `[[file:ID]]` marker the model emitted into a real
    `[name](url)` markdown link, using only URLs this process itself
    generated (media.file_url) — the model never sees or invents a URL.
    An id that isn't one of the files actually offered to it (shouldn't
    happen given the closed list in the prompt, but the model output isn't
    trusted) is silently dropped rather than left as a raw marker or a
    dead link."""

    def _replace(m: re.Match[str]) -> str:
        evidence = files_by_id.get(m.group(1))
        if evidence is None:
            return ""
        return f" [{evidence.name}]({evidence.url})"

    return re.sub(r"\s+([.,;:!?])", r"\1", _CITATION_RE.sub(_replace, text)).strip()


def _format_evidence(files: list[FileEvidence]) -> str:
    if not files:
        return "(нет файлов)"
    lines = []
    for f in files:
        kind_ru = "фото" if f.kind == "image" else "видео"
        if f.equipment:
            eq = ", ".join(f"{cls} × {n}" for cls, n in sorted(f.equipment.items()))
        else:
            eq = "техника не обнаружена"
        lines.append(f'- id={f.asset_id} [{kind_ru} «{f.name}»]: {eq}')
    return "\n".join(lines)


def _facts_to_prompt(facts: EntryFacts) -> str:
    if facts.phase_name:
        confidence_pct = round((facts.phase_confidence or 0) * 100)
        phase_line = f"{facts.phase_name} (уверенность модели {confidence_pct}%)"
    else:
        phase_line = "не определена"

    if facts.delay_days is None:
        delay_line = "недоступен"
    elif facts.delay_days > 0:
        delay_line = f"отставание {facts.delay_days} дн., ожидаемое завершение {facts.expected_completion}"
    elif facts.delay_days < 0:
        delay_line = f"опережение {-facts.delay_days} дн., ожидаемое завершение {facts.expected_completion}"
    else:
        delay_line = f"соответствует графику, ожидаемое завершение {facts.expected_completion}"

    return (
        f"Проект: {facts.project_name}\n"
        f"Дата записи: {facts.entry_date}\n"
        f"Текущая фаза (модель определения фазы): {phase_line}\n"
        f"Прогноз графика (модель отставания): {delay_line}\n\n"
        f"Материалы этой записи (используйте id из этого списка для маркеров "
        f"[[file:ID]], другие id использовать нельзя):\n{_format_evidence(facts.files)}"
    )


_REPORT_TOOL = {
    "name": "write_narrative_report",
    "description": "Short narrative construction-site status report.",
    "input_schema": {
        "type": "object",
        "properties": {"narrative": {"type": "string"}},
        "required": ["narrative"],
    },
}

_ENTRY_SYSTEM_PROMPT = """Вы — ассистент по мониторингу стройплощадки. По структурированным \
данным ниже напишите короткий (3-5 предложений) абзац на русском языке для записи в \
строительном журнале: какая сейчас фаза строительства, что показывают загруженные фото/видео \
этой записи, есть ли отставание от графика и чем оно объясняется.

Опирайтесь ТОЛЬКО на факты и список материалов ниже — не придумывайте цифры, даты, названия \
техники или файлы, которых нет в списке. Когда утверждение подтверждается конкретным фото или \
видео из списка (например, отсутствие техники в кадре объясняет отставание, или наоборот — \
техника есть и работа идёт по графику), сразу после утверждения вставьте маркер [[file:ID]], \
где ID — один из id из списка материалов. Используйте маркеры только для реальных id из \
списка, ничего не выдумывайте. Не используйте markdown-ссылки или другую разметку — только \
обычный текст и маркеры [[file:ID]]. Если данных о фазе или отставании нет, прямо напишите, \
что оценка недоступна, не оценивайте это на глаз. Ответьте только текстом, без заголовков и \
пояснений."""

_PROJECT_SYSTEM_PROMPT = """Вы — ассистент по мониторингу стройплощадки. По структурированным \
данным о последней проанализированной записи объекта ниже напишите короткую (2-4 предложения) \
сводку текущего состояния объекта на русском языке для руководителя проекта: на какой фазе \
строительства сейчас объект, соответствует ли график плану, и на чём основан вывод.

Опирайтесь ТОЛЬКО на факты и список материалов ниже — не придумывайте цифры, даты, названия \
техники или файлы, которых нет в списке. Когда утверждение подтверждается конкретным фото или \
видео из списка, сразу после утверждения вставьте маркер [[file:ID]], где ID — один из id из \
списка материалов. Используйте маркеры только для реальных id из списка, ничего не выдумывайте. \
Не используйте markdown-ссылки или другую разметку — только обычный текст и маркеры \
[[file:ID]]. Если данных недостаточно, прямо напишите об этом. Ответьте только текстом, без \
заголовков и пояснений."""


def _extract_json(text: str) -> dict:
    """Pulls the JSON object out of the model's response even if it wrapped
    it in ```json ... ``` — same tolerance plan_normalizer._extract_json
    has for the same model family."""
    text = text.strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("model response has no JSON object")
    return json.loads(text[start : end + 1])


def _call_llm(system: str, user: str) -> str:
    assert _client is not None
    schema = _REPORT_TOOL["input_schema"]
    system_full = (
        system
        + "\n\nReturn ONLY a JSON object that conforms to this JSON Schema:\n"
        + json.dumps(schema, ensure_ascii=False)
    )
    resp = _client.chat.completions.create(
        model=f"gpt://{YANDEX_CLOUD_FOLDER}/{YANDEX_CLOUD_MODEL}",
        messages=[{"role": "system", "content": system_full}, {"role": "user", "content": user}],
        temperature=0.3,
        max_tokens=800,
        response_format={
            "type": "json_schema",
            "json_schema": {"name": _REPORT_TOOL["name"], "schema": schema},
        },
    )
    out = _extract_json(resp.choices[0].message.content)
    narrative = str(out["narrative"]).strip()
    if not narrative:
        raise ValueError("model returned an empty narrative")
    return narrative


def _generate(system: str, facts: EntryFacts) -> str | None:
    if _client is None:
        return None
    try:
        narrative = _call_llm(system, _facts_to_prompt(facts))
    except Exception:  # best-effort, see module docstring
        log.exception("narrative generation failed")
        return None
    files_by_id = {str(f.asset_id): f for f in facts.files}
    return _resolve_citations(narrative, files_by_id)


def generate_entry_report(facts: EntryFacts) -> str | None:  # block 5, EXTENSION POINT
    return _generate(_ENTRY_SYSTEM_PROMPT, facts)


def generate_project_report(facts: EntryFacts) -> str | None:  # block 6, EXTENSION POINT
    return _generate(_PROJECT_SYSTEM_PROMPT, facts)
