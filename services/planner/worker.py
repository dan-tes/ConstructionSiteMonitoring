"""Planner service — LLM fallback for block 1 (диаграмма: блок 1).

Consumes `plan.command` from the shared `csm.analysis` exchange, publishes a
result on `plan.result`. See backend/integrations/README.md for the pipeline
topology this is one leg of, and backend/plan_parser.py for the fast path
this is the fallback for: that parser only accepts an already-canonical
21-column workbook. Most uploaded plans — an MS Project/Primavera/Excel
export with its own column names and its own names for work items — aren't
in that shape, so this service asks an LLM to re-express the plan on our 10
canonical phases instead.

The 10 phases and their expected equipment are not something a language
model is expected to invent correctly on its own — they're an empirically
derived reference (data/phase_equipment_reference.csv, sourced from the
project's own equipment-usage study; see phase_determination/data/ for the
canonical copy) fed into the prompt so the model has a closed vocabulary to
map onto instead of free-text guessing. The model still has to do the hard
part: read whatever the source plan's own phases/work items are called and
decide which canonical phase(s) each maps to, and roughly how many days of
that phase's duration each accounts for.

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
from datetime import datetime, timezone
from pathlib import Path

import aio_pika
import httpx
import openai
import pandas as pd

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
# Cap on how much of the source plan's table gets forwarded to the model —
# keeps a large uploaded schedule inside a sane token budget. A canonical
# plan is thousands of activity rows, but a free-form one being normalized
# here is normally a hundred phase-level line items or fewer, so this should
# rarely bind; it truncates rather than fails when it does, since a partial
# read is still better than none.
MAX_SOURCE_ROWS = 500

_client = openai.OpenAI(
    api_key=YANDEX_CLOUD_API_KEY,
    base_url="https://ai.api.cloud.yandex.net/v1",
    project=YANDEX_CLOUD_FOLDER,
)


def _build_reference_table_text() -> str:
    ref = pd.read_csv(DATA_DIR / "phase_equipment_reference.csv")
    lines = []
    for phase in CANONICAL_PHASES:
        rows = ref[ref["phase"] == phase]
        if rows.empty:
            continue
        equipment = ", ".join(
            f"{r.equipment_class} ({r.tier}, ~{r.presence_pct:.0f}% of projects, "
            f"{r.count_min}-{r.count_max} units)"
            for r in rows.itertuples()
        )
        lines.append(f"{CANONICAL_PHASES.index(phase) + 1}. {phase}: {equipment}")
    return "\n".join(lines)


def _build_system_prompt() -> str:
    numbered_phases = "\n".join(f"{i + 1}. {p}" for i, p in enumerate(CANONICAL_PHASES))
    return f"""You are a construction-scheduling assistant. You will be given a \
free-form construction schedule/plan (arbitrary columns, arbitrary phase or \
work-item names, possibly in Russian or English) as a table. Your job is to \
re-express it using exactly these 10 canonical construction phases, in this \
fixed order, and no others:

{numbered_phases}

For each canonical phase that the source plan has any work mapping to, \
estimate its total planned duration in days (if the source gives dates, use \
them; if it gives a duration per work item, sum the ones that map to that \
phase; use judgement for work that clearly overlaps in time). Omit a \
canonical phase entirely if the source plan has no matching work — do not \
invent a phase that isn't represented.

Also pick, for each included phase, which equipment classes are expected on \
site during it, using ONLY these exact class names: {", ".join(EQUIPMENT_CLASSES)}.
The reference table below (empirically derived from real projects) shows how \
often each class shows up per phase — use it as your prior, adjusted by \
whatever equipment the source plan itself mentions for that phase:

{_build_reference_table_text()}

Finally, estimate project_duration_days: the total project critical-path \
duration in days from start to finish (use an explicit total or an overall \
date range from the source plan if it gives one; otherwise sum the phase \
durations you estimated, discounted for the overlap typical between \
adjacent phases).

Respond with ONLY a single JSON object, no markdown code fences, no \
commentary before or after it, matching exactly this shape:
{{"project_duration_days": <number>, "phases": [{{"phase": "<one of the 10 \
names above, exact spelling>", "planned_duration_days": <number>, \
"expected_equipment": ["<equipment class>", ...]}}]}}"""


SYSTEM_PROMPT = _build_system_prompt()


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


def _parse_model_output(text: str) -> tuple[list[NormalizedPhase], float]:
    text = text.strip()
    if text.startswith("```"):
        # Strip a ```json ... ``` fence if the model added one despite being
        # told not to — tolerate the formatting quirk, not the content.
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    data = json.loads(text)
    project_duration_days = float(data["project_duration_days"])
    all_phases = [NormalizedPhase.model_validate(p) for p in data["phases"]]

    # A phase with a non-positive duration is the model including a phase it
    # was told to omit (observed live: an unmatched "Commissioning" comes
    # back at 0 days instead of being left out) — drop just that phase
    # rather than failing the whole normalization over one harmless zero.
    # Model output isn't guessed at here, just filtered on an unambiguous
    # signal; a malformed *phase name* still fails loudly (see the Literal
    # validation in NormalizedPhase above) since there's no safe filter for
    # that.
    phases = [p for p in all_phases if p.planned_duration_days > 0]
    if not phases:
        raise ValueError("model returned no phases with a positive duration")
    seen: set[str] = set()
    for p in phases:
        if p.phase in seen:
            raise ValueError(f"duplicate phase {p.phase!r} in model output")
        seen.add(p.phase)
    if project_duration_days <= 0:
        raise ValueError("non-positive project_duration_days")
    return phases, project_duration_days


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
    completion = _client.responses.create(
        model=f"gpt://{YANDEX_CLOUD_FOLDER}/{YANDEX_CLOUD_MODEL}",
        temperature=0.2,
        instructions=SYSTEM_PROMPT,
        input=plan_text,
        max_output_tokens=6000,
        store=True,
        # "none" — yandexgpt-5-lite is a non-reasoning model; a "low"/"medium"
        # effort value here is untested against the real API and may simply
        # be rejected, so this matches the confirmed-working call verbatim
        # rather than guessing.
        reasoning={"effort": "none"},
        truncation="auto",
    )
    phases, project_duration_days = _parse_model_output(completion.output_text)
    log.info(
        "normalized plan for asset %s into %d phases, %.0f total days",
        command.asset_id,
        len(phases),
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
