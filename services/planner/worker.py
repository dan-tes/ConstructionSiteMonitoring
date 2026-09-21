"""Planner service — LLM fallback for block 1 (диаграмма: блок 1).

Consumes `plan.command` from the shared `csm.analysis` exchange, publishes a
result on `plan.result`. See backend/integrations/README.md for the pipeline
topology this is one leg of, and backend/plan_parser.py for the fast path
this is the fallback for: that parser only accepts an already-canonical
21-column workbook. Most uploaded plans — an MS Project/Primavera/Excel
export with its own column names and its own names for work items — aren't
in that shape, so this service normalizes the plan onto our 10 canonical
phases instead.

The phase/equipment breakdown is produced by `plan_normalizer.normalize()`
(see that module's docstring): a two-stage pipeline where an LLM only does
the two things that need "meaning" — reading the source table's structure,
and classifying each row's work into a CLOSED vocabulary of activity types —
while ordinary code does everything else (numbers, units, validation). The
closed vocabulary (43 activity types -> phase -> expected equipment) comes
from `data/canonical_plan.xlsx`, an empirical reference, not something the
model is asked to invent. This replaces the previous single-prompt design
that asked the model to guess whole-phase durations directly; classifying
per activity and letting code sum durations per phase is both more accurate
and auditable (`normalize()`'s log/problems explain every row's fate).

`plan_normalizer.normalize()` deliberately does not attempt an overall
project (critical-path) duration — the source plan has no dependency graph
to derive one from, so a per-activity classifier has nothing to base it on
without guessing. `project_duration_days` is still produced by a single,
narrow LLM call here (`_estimate_project_duration`) that only asks for that
one number, using judgement about how much adjacent phases typically
overlap — the same kind of holistic estimate the old single-prompt design
made, just no longer bundled with the (now per-activity) phase breakdown.

REAL model: Yandex Cloud's OpenAI-compatible endpoint (YandexGPT) — see the
YANDEX_CLOUD_* env vars below; the API key has no default and the service
refuses to start without one (never hardcode it, see README's Open items).
Any invalid/unparseable model output is a `failed` result, never a guess
passed downstream silently: a wrong equipment expectation is harmless (it
only ever softens a phase-match score elsewhere), but a wrong phase name or
duration would poison the delay forecast, so this fails loudly instead of
coercing bad output into something that merely looks valid.
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import aio_pika
import httpx
import openai
import pandas as pd

import plan_normalizer
from schemas import (
    CANONICAL_PHASES,
    EQUIPMENT_CLASSES,
    Envelope,
    NormalizedPhase,
    PlanNormalizeCommand,
    PlanNormalizeResult,
)

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("csm.planner")

RABBITMQ_URL = os.environ.get("RABBITMQ_URL", "amqp://guest:guest@localhost:5672/")
EXCHANGE_NAME = "csm.analysis"  # must match backend/integrations/broker.py
COMMAND_ROUTING_KEY = "plan.command"
RESULT_ROUTING_KEY = "plan.result"
COMMAND_QUEUE = "plan.command.q"

YANDEX_CLOUD_FOLDER = os.environ["YANDEX_CLOUD_FOLDER"]
# Never hardcode this — set it via the environment (docker-compose.yml reads
# it from the host's own env / .env, same as every other secret in this repo).
YANDEX_CLOUD_API_KEY = os.environ["YANDEX_CLOUD_API_KEY"]
YANDEX_CLOUD_MODEL = os.environ.get("YANDEX_CLOUD_MODEL", "yandexgpt-5-lite/latest")

DATA_DIR = Path(__file__).parent / "data"
CANONICAL_PLAN_PATH = DATA_DIR / "canonical_plan.xlsx"
# Cross-request cache of (activity name + section context) -> classification
# decision (see plan_normalizer.normalize()'s `key()`/`cache`). Worth keeping
# across different projects' plans, not just within one: unrelated customer
# plans routinely reuse the same activity names ("Опалубка колонн", "Footing
# Concrete", ...), so this is a real hit rate, not a one-shot optimization.
CLASSIFICATION_CACHE_PATH = DATA_DIR / "classification_cache.json"
# Cap on how much of the source plan's table gets forwarded to the model for
# the project-duration estimate below — keeps a large uploaded schedule
# inside a sane token budget. A canonical plan is thousands of activity
# rows, but a free-form one being normalized here is normally a hundred
# line items or fewer, so this should rarely bind; it truncates rather than
# fails when it does, since a partial read is still better than none.
MAX_SOURCE_ROWS = 500

_client = openai.OpenAI(
    api_key=YANDEX_CLOUD_API_KEY,
    base_url="https://ai.api.cloud.yandex.net/v1",
    project=YANDEX_CLOUD_FOLDER,
    # AI Studio logs requests by default; an uploaded customer plan can be
    # confidential (same precaution plan_normalizer.py takes on its own).
    default_headers={"x-data-logging-enabled": "false"},
)

# Fail fast at import time, not on the first request, if the reference
# vocabulary is missing/malformed or (implausibly) uses phase names that
# don't match the CanonicalPhase contract the rest of the pipeline relies on.
_startup_vocab = plan_normalizer.Vocab(str(CANONICAL_PLAN_PATH))
_unknown_phases = set(_startup_vocab.phases) - set(CANONICAL_PHASES)
if _unknown_phases:
    raise RuntimeError(
        f"{CANONICAL_PLAN_PATH} has phases not in the CanonicalPhase contract: {_unknown_phases}"
    )
del _startup_vocab, _unknown_phases


def _llm_call(system: str, user: str, tool: dict) -> dict:
    """Adapter matching plan_normalizer's `LLM` callable, backed by this
    service's own Yandex client/env vars instead of plan_normalizer's own
    (differently-named) ones. Same call shape as plan_normalizer.yandex_llm:
    Yandex's OpenAI-compatible Completions API with structured (json_schema)
    output — one consistent call style for every LLM call this service
    makes (structure detection, activity classification, and the duration
    estimate below)."""
    schema = tool["input_schema"]
    system = (
        system
        + "\n\nReturn ONLY a JSON object that conforms to this JSON Schema:\n"
        + json.dumps(schema, ensure_ascii=False)
    )
    resp = _client.chat.completions.create(
        model=f"gpt://{YANDEX_CLOUD_FOLDER}/{YANDEX_CLOUD_MODEL}",
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        temperature=0,
        max_tokens=4000,
        response_format={"type": "json_schema", "json_schema": {"name": tool["name"], "schema": schema}},
    )
    return plan_normalizer._extract_json(resp.choices[0].message.content)


def _plan_text_from_bytes(original_name: str, content: bytes) -> str:
    ext = original_name.rsplit(".", 1)[-1].lower() if "." in original_name else ""
    if ext == "csv":
        df = pd.read_csv(io.BytesIO(content))
    elif ext in ("xlsx", "xls"):
        df = pd.read_excel(io.BytesIO(content))
    else:
        raise ValueError(f"unsupported plan file extension: {ext!r}")
    if len(df) > MAX_SOURCE_ROWS:
        log.info("plan has %d rows, truncating to %d for the prompt", len(df), MAX_SOURCE_ROWS)
        df = df.head(MAX_SOURCE_ROWS)
    return df.to_csv(index=False)


_DURATION_TOOL = {
    "name": "estimate_project_duration",
    "description": "Overall project critical-path duration, in days.",
    "input_schema": {
        "type": "object",
        "properties": {"project_duration_days": {"type": "number"}},
        "required": ["project_duration_days"],
    },
}

_DURATION_SYSTEM_PROMPT = """You are a construction-scheduling assistant. You are given a \
free-form construction schedule/plan (arbitrary columns, arbitrary phase or work-item \
names, possibly in Russian or English) as a table.

Estimate project_duration_days: the total project critical-path duration in days from \
start to finish. Use an explicit total or an overall start/finish date range if the \
source plan gives one; otherwise sum the durations of the individual work items, \
discounted for how much adjacent/overlapping work typically runs in parallel on a \
construction site. Respond with a single JSON object only: no markdown, no commentary."""


def _estimate_project_duration(plan_text: str) -> float:
    out = _llm_call(_DURATION_SYSTEM_PROMPT, f"Schedule:\n{plan_text}", _DURATION_TOOL)
    value = float(out["project_duration_days"])
    if value <= 0:
        raise ValueError("non-positive project_duration_days")
    return value


def _aggregate_phases(plan_df: pd.DataFrame) -> list[NormalizedPhase]:
    """Per-activity canonical rows -> per-phase totals. Durations are summed
    (not estimated) and equipment is the union of what each included
    activity expects — both plain code, no LLM judgement involved, since
    plan_normalizer.normalize() already did the only part that needed it
    (classifying each activity)."""
    phases = []
    for (_, phase), rows in plan_df.groupby(["phase_order", "phase"], sort=True):
        equipment: set[str] = set()
        for eq in rows["expected_equipment"]:
            equipment.update(e for e in eq if e in EQUIPMENT_CLASSES)
        phases.append(
            NormalizedPhase(
                phase=phase,
                planned_duration_days=float(rows["planned_duration_days"].dropna().sum()),
                expected_equipment=sorted(equipment),
            )
        )
    if not phases:
        raise ValueError("plan_normalizer produced no phases")
    return phases


def compute(command: PlanNormalizeCommand) -> PlanNormalizeResult:  # EXTENSION POINT
    response = httpx.get(command.plan_url, timeout=30.0)
    response.raise_for_status()
    plan_text = _plan_text_from_bytes(command.original_name, response.content)

    log.info(
        "normalizing plan for asset %s (%d chars of source table) via %s",
        command.asset_id,
        len(plan_text),
        YANDEX_CLOUD_MODEL,
    )

    ext = command.original_name.rsplit(".", 1)[-1].lower() if "." in command.original_name else "csv"
    with tempfile.NamedTemporaryFile(suffix=f".{ext}") as tmp:
        tmp.write(response.content)
        tmp.flush()
        plan_df, _log_df, problems, _vocab = plan_normalizer.normalize(
            tmp.name,
            str(CANONICAL_PLAN_PATH),
            str(command.asset_id),
            llm=_llm_call,
            cache_path=str(CLASSIFICATION_CACHE_PATH),
        )

    hard_problems = [p for p in problems if not p.startswith("WARNING")]
    if hard_problems or plan_df.empty:
        raise ValueError(
            "plan_normalizer rejected this plan: " + "; ".join(hard_problems or ["empty plan"])
        )

    phases = _aggregate_phases(plan_df)
    project_duration_days = _estimate_project_duration(plan_text)

    log.info(
        "normalized plan for asset %s into %d phases (%d source activities), %.0f total days",
        command.asset_id,
        len(phases),
        len(plan_df),
        project_duration_days,
    )
    return PlanNormalizeResult(
        status="done", phases=phases, project_duration_days=project_duration_days
    )


async def main() -> None:
    connection = await aio_pika.connect_robust(RABBITMQ_URL)
    async with connection:
        channel = await connection.channel()
        await channel.set_qos(prefetch_count=1)
        exchange = await channel.declare_exchange(
            EXCHANGE_NAME, aio_pika.ExchangeType.TOPIC, durable=True
        )
        queue = await channel.declare_queue(COMMAND_QUEUE, durable=True)
        await queue.bind(exchange, COMMAND_ROUTING_KEY)

        async def on_message(message: aio_pika.abc.AbstractIncomingMessage) -> None:
            async with message.process():
                envelope = Envelope[PlanNormalizeCommand].model_validate_json(message.body)
                try:
                    result = await asyncio.to_thread(compute, envelope.payload)
                except Exception as exc:  # noqa: BLE001 - reported back as a failed result
                    log.exception("plan normalization failed for asset %s", envelope.correlation_id)
                    result = PlanNormalizeResult(status="failed", error=str(exc))

                reply = Envelope(
                    correlation_id=envelope.correlation_id,
                    published_at=datetime.now(timezone.utc),
                    payload=result,
                )
                await exchange.publish(
                    aio_pika.Message(
                        body=reply.model_dump_json().encode(),
                        delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                    ),
                    routing_key=RESULT_ROUTING_KEY,
                )

        await queue.consume(on_message)
        log.info("planner worker consuming %s from %s", COMMAND_ROUTING_KEY, COMMAND_QUEUE)
        await asyncio.Future()  # run forever


if __name__ == "__main__":
    asyncio.run(main())
