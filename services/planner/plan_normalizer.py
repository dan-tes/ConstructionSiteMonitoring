"""
Модуль 1. Приведение календарного плана заказчика (любой формат) к каноническому виду.

Принцип: LLM НЕ пишет итоговую таблицу. Она делает две вещи, где нужен «смысл»:
  Этап 1. Понимает структуру файла (какая колонка что содержит, в каких единицах).
  Этап 2. Классифицирует названия работ в закрытый словарь (43 вида работ / 10 фаз).
Всё остальное (числа, единицы, ID, порядок, техника, валидация) делает обычный код.

Словарь и связка «вид работ -> фаза -> техника» берутся из эталонного canonical_plan.xlsx,
а не копируются руками.

Запуск в PyCharm: заполните блок «НАСТРОЙКИ» ниже, создайте рядом файл .env с ключами
(см. ниже) и нажмите Run на этом файле. Аргументы и export не нужны.

Файл .env (рядом с plan_normalizer.py, в .gitignore!):
    YANDEX_API_KEY=ключ_сервисного_аккаунта
    YANDEX_FOLDER_ID=идентификатор_каталога
    YANDEX_MODEL=yandexgpt/rc            # необязательно
Для чтения .env нужно один раз: pip install python-dotenv

Из другого файла проекта:
    from plan_normalizer import run
    plan, log, problems = run("data/raw/customer_plan.csv",
                              "data/plan/activities_with_equipment.csv", "P00042",
                              out_dir="data/plan_normalized")

Из терминала (по желанию): python plan_normalizer.py файл.csv --project-id P00042

Вход: .csv (UTF-8 или cp1251, разделитель определяется сам) или .xlsx (первый лист).
"""
from __future__ import annotations

import argparse
import ast
from collections import Counter
import csv
import difflib
import io
import json
import math
import os
import re
import statistics
import sys
import warnings
from pathlib import Path
from typing import Callable

import pandas as pd

BASE_DIR = Path(__file__).resolve().parent

try:                                   # ключи из файла .env (рядом со скриптом или в рабочей папке)
    from dotenv import load_dotenv
    load_dotenv(BASE_DIR / ".env", override=True)   # .env важнее переменных ОС, чтобы старый ключ не перекрывал новый
    load_dotenv()
except ImportError:
    pass

# =========================== НАСТРОЙКИ (для запуска кнопкой Run) =========================== #
# Относительные пути считаются от папки, где лежит этот файл.
SRC_PATH = "zakaz1.csv"                        # план заказчика (.csv или .xlsx)
VOCAB_PATH = "data/canonical_plan.xlsx"    # эталонный канонический план
PROJECT_ID = "P00042"                                          # попадёт в колонку project_id
OUT_DIR = "plan_normalized"                               # куда сохранить результат
SAVE_XLSX = False                                              # True — ещё и .xlsx
CSV_SEP = None                                                 # None — определить разделитель сам; или ";" / ","
# ============================================================================================ #

YANDEX_BASE_URL = "https://ai.api.cloud.yandex.net/v1"   # OpenAI-совместимый Completions API
DEFAULT_MODEL = "yandexgpt/rc"
ROW_KINDS = ("work", "summary", "milestone")
COLUMN_KEYS = ["name", "id", "outline_level", "duration", "start", "finish", "criticality", "status"]
UNIT_KINDS = ("days", "working_days", "weeks", "text_with_units", "unknown")
UNMAPPED = "UNMAPPED"
MAX_OUTLINE_LEVELS = 8            # больше разных «уровней» — это, скорее всего, нумерация строк, а не иерархия
BATCH_SIZE = 25
REVIEW_THRESHOLD = 0.75          # ниже — строка уходит эксперту на проверку
WORKDAYS_PER_WEEK = 5

EQUIPMENT_CLASSES = [
    "worker", "tower_crane", "hanging_hook", "vehicle_crane", "roller", "bulldozer",
    "excavator", "truck", "loader", "pump_truck", "concrete_mixer", "pile_driver",
    "other_vehicle",
]
CANON_COLS = [
    "project_id", "activity_id", "phase", "phase_order", "activity_sequence",
    "activity_name", "planned_duration_days", "criticality", "status", "expected_equipment",
]

# LLM-вызов: (system, user, tool_schema) -> dict, соответствующий tool["input_schema"]. Подменяется в тестах.
LLM = Callable[[str, str, dict], dict]


# --------------------------------------------------------------------------- #
# 0. Словарь из эталона
# --------------------------------------------------------------------------- #
class Vocab:
    """Словарь «вид работ -> фаза -> техника» из эталонного плана (.csv или .xlsx)."""

    def __init__(self, path: str, equipment_descriptions_path: str | None = None):
        p = Path(path)
        if p.suffix.lower() in (".xlsx", ".xlsm"):
            plan = pd.read_excel(p, sheet_name="plan", dtype=str)
            self.equip_desc = pd.read_excel(p, sheet_name="equipment_descriptions")
        else:
            plan = pd.read_csv(p, dtype=str, encoding="utf-8-sig")
            self.equip_desc = self._load_equip_csv(p, equipment_descriptions_path)
        missing = {"activity_name", "phase", "phase_order", "expected_equipment"} - set(plan.columns)
        if missing:
            raise ValueError(f"В эталонном плане {path} нет колонок: {sorted(missing)}")

        # Одно название вида работ должно давать одну фазу и один набор техники.
        eq_key = plan["expected_equipment"].map(lambda x: tuple(sorted(ast.literal_eval(x))))
        n = pd.DataFrame({"activity_name": plan["activity_name"], "phase": plan["phase"],
                          "eq": eq_key}).groupby("activity_name").nunique()
        bad = n[(n[["phase", "eq"]] > 1).any(axis=1)].index.tolist()
        if bad:
            print(f"WARNING: в эталоне у видов работ {bad} несколько разных фаз/наборов техники; "
                  f"берётся первое вхождение")

        self.activities: dict[str, dict] = {}
        for r in plan.drop_duplicates("activity_name").itertuples():
            self.activities[r.activity_name] = {
                "phase": r.phase,
                "phase_order": int(r.phase_order),
                "equipment": ast.literal_eval(r.expected_equipment),
            }
        phases = plan.drop_duplicates("phase")[["phase", "phase_order"]]
        self.phases = {p_: int(o) for p_, o in sorted(zip(phases.phase, phases.phase_order.astype(int)),
                                                      key=lambda x: x[1])}

    @staticmethod
    def _load_equip_csv(plan_path: Path, explicit: str | None) -> pd.DataFrame:
        candidates = [Path(explicit)] if explicit else []
        candidates += [plan_path.parent / "equipment_descriptions.csv",
                       plan_path.parent.parent / "equipment" / "equipment_descriptions.csv"]
        for c in candidates:
            if c.exists():
                return pd.read_csv(c, encoding="utf-8-sig")
        print("WARNING: equipment_descriptions.csv не найден, лист с описаниями техники будет пустым")
        return pd.DataFrame(columns=["equipment_class", "description"])

    def catalog_text(self) -> str:
        lines = []
        for phase, order in self.phases.items():
            lines.append(f"Phase {order} - {phase}:")
            for name, a in self.activities.items():
                if a["phase"] == phase:
                    lines.append(f"  - {name}  (typical equipment: {', '.join(a['equipment'])})")
        return "\n".join(lines)


# --------------------------------------------------------------------------- #
# 1. Промпты и схемы tool
# --------------------------------------------------------------------------- #
SYSTEM_SCHEMA = """You are a module that parses the structure of a construction schedule file.
You are shown the first rows of a raw table. The left-most value of every line is the row number of the
file and is NOT part of the table. Your job is to find the header row and say which column holds what.
You do not rewrite, recalculate or correct anything.

You identify a column by the EXACT text of its header cell, copied character by character from the header
row (same language, same spelling, same punctuation). Never translate, shorten or paraphrase a header.
Never use column numbers. If there is no such column, return an empty string "".

The file may be in Russian or English (e.g. "Наименование задачи" = task name, "Длительность" =
duration, "Начало"/"Окончание" = start/finish, "Трудозатраты" = labour effort).

Rules:
1. name is the column with the TEXT names of the tasks (words such as "Разработка котлована").
   It is never a column with numbers, dates, durations or codes.
2. Never pick a column that merely looks similar when its meaning is different
   (e.g. "Labour, man-hours" is not duration; "Cost" is not criticality).
3. duration is the planned duration of the task. If there is no duration column but there are start and
   finish dates, fill start and finish and return "" for duration.
4. duration_unit: days / working_days / weeks / text_with_units (units are written inside the cells,
   e.g. "26 дн.", "2 нед.", "5 days") / unknown. If the units are not visible, answer unknown - do not guess.
5. criticality is a NUMERIC criticality only. Categories (High/Medium/Low, "Высокий/Средний/Низкий")
   and critical-path flags (Yes/No) do NOT count; mention them in notes instead.
6. id is the column with a unique task ID or code ("№", "ID", "WBS"). outline_level is the column with the
   nesting level (1, 2, 3...) or a WBS code ("1.2.3"). The same column may be used for both.
7. If the file contains several projects or sheets with different structures, describe this in notes.
Respond with a single JSON object only: no markdown, no commentary."""

TOOL_SCHEMA = {
    "name": "describe_source_table",
    "description": "Description of the structure of the source schedule table.",
    "input_schema": {
        "type": "object",
        "properties": {
            "columns": {
                "type": "object",
                "description": "Exact header text of the column, or an empty string if there is no such column",
                "properties": {k: {"type": "string"} for k in COLUMN_KEYS},
                "required": COLUMN_KEYS,
            },
            "duration_unit": {"type": "string", "enum": list(UNIT_KINDS)},
            "language": {"type": "string"},
            "notes": {"type": "string"},
        },
        "required": ["columns", "duration_unit", "language", "notes"],
    },
}


def build_classify_system(v: Vocab) -> str:
    return f"""You are an expert in construction schedules (Russian GESN/FER estimating norms, MS Project,
Primavera; the input may be in Russian or English). For every row of a schedule, decide which activity type
from a CLOSED LIST it corresponds to. The result is used to determine the construction phase from the
equipment seen on site, so a wrong match is worse than an honest UNMAPPED.

CLOSED LIST OF ACTIVITY TYPES (canonical_activity), grouped by phase:
{v.catalog_text()}

ALLOWED EQUIPMENT CLASSES (only these): {', '.join(EQUIPMENT_CLASSES)}

RULES
1. canonical_activity is exactly one value from the list above, or UNMAPPED. No other values are allowed.
2. Match by the meaning of the work, not by shared words. Floor, work zone, section, axis, quantity and
   other detail in the name are ignored: "Армирование колонн 3 этажа" (reinforcement of 3rd-floor columns)
   = Column Reinforcement.
3. The technological operation must match: reinforcement, formwork and concrete pouring are different
   activity types even when they concern the same structure.
4. If the work is technologically absent from the list (waterproofing, roofing, piling, facades, utility
   networks, etc.), use UNMAPPED. Never pick the "closest sounding" type.
   For UNMAPPED work (row_kind="work") you MUST give phase_if_unmapped as one of the 10 phase names from
   the list above and proposed_equipment: only equipment that is really typical for this work, nothing
   extra. phase_if_unmapped = "NONE" is FORBIDDEN in this case, even if you are unsure — use the parents
   (section headings) and where the row sits among the other rows to pick the closest matching phase; a
   plausible phase beats "NONE" here.
   For every other row (canonical_activity is not UNMAPPED, or row_kind is "summary"/"milestone")
   phase_if_unmapped = "NONE" and proposed_equipment = [].
5. parents are the section headings the row sits under. Use them as context (the same "Бетонирование"
   (concreting) under "Фундаменты" (foundations) and under "Каркас" (frame) are different activity types).
6. row_kind: summary = a section heading with no work of its own; milestone = a milestone ("Start",
   "Handover", zero duration); work = everything else. For summary and milestone set
   canonical_activity = UNMAPPED.
7. confidence is between 0 and 1. If two activity types are plausible, do not go above 0.6.
   evidence is at most 15 words, in English: why you chose this type.
8. Do not add or skip rows: return exactly the row_ids you were given.

EXAMPLES (parents > name  =>  result)
- [Нулевой цикл > Фундаменты] "Бетонирование монолитных ростверков" (concreting of monolithic pile caps)
  => Footing Concrete, 0.8
- [Нулевой цикл] "Разработка грунта в котловане" (excavation of the pit)  => Excavation, 0.95
- [Нулевой цикл > Фундаменты] "Гидроизоляция фундаментной плиты" (waterproofing of the foundation slab)
  => UNMAPPED, phase_if_unmapped=Foundation, proposed_equipment=[worker], 0.9 (waterproofing is not in the list)
- [Отделка] "Монтаж оконных блоков ПВХ" (installation of PVC window units)  => Doors and Windows, 0.9
- [] "Начало строительства" (start of construction)  => row_kind=milestone, UNMAPPED
Respond with a single JSON object only: no markdown, no commentary."""


def classify_tool(v: Vocab) -> dict:
    return {
        "name": "classify_tasks",
        "description": "Classification of schedule rows into the closed vocabulary of activity types.",
        "input_schema": {
            "type": "object",
            "properties": {"items": {"type": "array", "items": {
                "type": "object",
                "properties": {
                    "row_id": {"type": "integer"},
                    "row_kind": {"type": "string", "enum": ["work", "summary", "milestone"]},
                    "canonical_activity": {"type": "string", "enum": list(v.activities) + [UNMAPPED]},
                    "phase_if_unmapped": {"type": "string", "enum": list(v.phases) + ["NONE"]},
                    "proposed_equipment": {"type": "array",
                                           "items": {"type": "string", "enum": EQUIPMENT_CLASSES}},
                    "confidence": {"type": "number"},
                    "evidence": {"type": "string"},
                },
                "required": ["row_id", "row_kind", "canonical_activity", "phase_if_unmapped",
                             "proposed_equipment", "confidence", "evidence"],
            }}},
            "required": ["items"],
        },
    }


# --------------------------------------------------------------------------- #
# 2. Yandex Cloud AI Studio (OpenAI-совместимый Completions API + structured output)
# --------------------------------------------------------------------------- #
def _extract_json(text: str) -> dict:
    """Достаёт JSON-объект из ответа, даже если модель обернула его в ```json ... ```."""
    text = text.strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("в ответе модели нет JSON-объекта")
    return json.loads(text[start:end + 1])


def yandex_llm(system: str, user: str, tool: dict) -> dict:
    absent = [k for k in ("YANDEX_API_KEY", "YANDEX_FOLDER_ID") if not os.environ.get(k)]
    if absent:
        raise RuntimeError(f"Не заданы {', '.join(absent)}. Создайте файл .env рядом с plan_normalizer.py "
                           f"(и pip install python-dotenv) либо укажите их в Run Configuration PyCharm.")
    import openai  # pip install openai

    folder = os.environ["YANDEX_FOLDER_ID"]
    client = openai.OpenAI(
        api_key=os.environ["YANDEX_API_KEY"],                     # ключ — только из окружения
        base_url=os.environ.get("YANDEX_BASE_URL", YANDEX_BASE_URL),
        project=folder,
        # по умолчанию AI Studio логирует запросы; в планах заказчика может быть конфиденциальное
        default_headers={"x-data-logging-enabled": "false"},
    )
    schema = tool["input_schema"]
    # Схему дублируем текстом: даже если ограничение декодирования не строгое, модель знает форму ответа.
    system = (system + "\n\nReturn ONLY a JSON object that conforms to this JSON Schema:\n"
              + json.dumps(schema, ensure_ascii=False))
    resp = client.chat.completions.create(
        model=f"gpt://{folder}/{os.environ.get('YANDEX_MODEL', DEFAULT_MODEL)}",
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        temperature=0,
        max_tokens=4000,
        response_format={"type": "json_schema",
                         "json_schema": {"name": tool["name"], "schema": schema}},
    )
    return _extract_json(resp.choices[0].message.content)


# --------------------------------------------------------------------------- #
# 3. Детерминированные помощники
# --------------------------------------------------------------------------- #
def _is_nan(x) -> bool:
    return x is None or (isinstance(x, float) and math.isnan(x))


def parse_duration(value, unit_hint: str) -> tuple[float | None, str | None]:
    """-> (дни | None, флаг для журнала). Единицу не угадываем молча."""
    if _is_nan(value):
        return None, None
    if isinstance(value, (int, float)):
        num, unit = float(value), unit_hint
    else:
        m = re.match(r"\s*(\d+(?:[.,]\d+)?)\s*(.*)$", str(value).strip().lower())
        if not m:
            return None, "duration_unparsed"
        num, suffix = float(m.group(1).replace(",", ".")), m.group(2)
        unit = ("weeks" if suffix.startswith(("н", "w")) else
                "days" if suffix.startswith(("д", "d")) else
                "months" if suffix.startswith(("мес", "mo")) else
                "hours" if suffix.startswith(("ч", "h")) else unit_hint)
    if unit in ("days", "working_days"):
        return num, None
    if unit == "weeks":
        return num * WORKDAYS_PER_WEEK, f"weeks_x{WORKDAYS_PER_WEEK}"
    if unit in ("unknown", "text_with_units", None):
        return num, "duration_unit_assumed_days"
    return None, "duration_unit_unsupported"


def read_table(path: str, sep: str | None = None) -> pd.DataFrame:
    """Сырая таблица без заголовка (нумерация колонок с 0). Понимает .xlsx и .csv."""
    p = Path(path)
    if p.suffix.lower() in (".xlsx", ".xlsm", ".xls"):
        return pd.read_excel(p, header=None, sheet_name=0)
    text = None
    for enc in ("utf-8-sig", "cp1251"):                     # Excel в России часто сохраняет cp1251
        try:
            text = p.read_text(encoding=enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        raise ValueError(f"Не удалось прочитать {path}: ожидается UTF-8 или cp1251")
    if sep is None:                                          # разделитель — по медиане числа вхождений
        lines = [ln for ln in text.splitlines() if ln.strip()][:50]
        sep = max(";\t,|", key=lambda d: statistics.median(ln.count(d) for ln in lines))
    rows = list(csv.reader(io.StringIO(text), delimiter=sep))
    width = max((len(r) for r in rows), default=0)
    # пустые ячейки -> None; пустые строки сохраняем, чтобы номера строк совпадали с файлом
    return pd.DataFrame([[(c.strip() or None) for c in r] + [None] * (width - len(r)) for r in rows],
                        dtype=object)


def _outline_level(v, wbs_mode: bool) -> int | None:
    """Уровень вложенности: число (1,2,3) или WBS-код (1.2.3). В .csv всё приходит строками, поэтому
    «2» — это уровень 2, если в колонке нет кодов с точкой; иначе колонка — WBS, и «2» = верхний уровень."""
    if _is_nan(v):
        return None
    t = str(int(v)) if isinstance(v, float) and v.is_integer() else str(v).strip()
    if re.fullmatch(r"\d+(\.\d+)+", t):
        return t.count(".") + 1
    if re.fullmatch(r"\d+", t):
        return 1 if wbs_mode else int(t)
    return None


_DUR_RE = re.compile(r"\d+(?:[.,]\d+)?\s*(?:дней|день|дн|нед|мес|days|day|wk|mo|d|w|ч|h)?\.?", re.I)
_DATE_RE = re.compile(r"\d{1,4}[./-]\d{1,2}[./-]\d{1,4}")


def _norm_hdr(x) -> str:
    """Заголовок для сравнения: без регистра, знаков препинания и лишних пробелов."""
    return re.sub(r"[^\w]+", " ", str(x).lower()).strip()


def _is_non_name(v) -> bool:
    """Значение НЕ похоже на название работы: число, дата, длительность, код."""
    t = str(v).strip()
    return bool(_DUR_RE.fullmatch(t) or _DATE_RE.fullmatch(t) or re.fullmatch(r"[\d.\s]+", t)
                or not re.search(r"[A-Za-zА-Яа-яЁё]{3,}", t))


def resolve_structure(raw: pd.DataFrame, names: dict):
    """Ответ модели (тексты заголовков) -> (номер строки заголовка, {ключ: номер колонки | None}, проблемы, предупреждения).
    Номера колонок определяет КОД по тексту заголовков: модель на счёте позиций ошибается (сдвиг на 1)."""
    want = {k: str(names.get(k) or "").strip() for k in COLUMN_KEYS}
    if not want["name"]:
        return None, None, ["`name` is empty, but the column with task names is required."], []

    best_row, best_hits = None, 0                    # строка заголовка = где нашлось больше названных колонок
    for i in range(min(30, len(raw))):
        cells = {_norm_hdr(c) for c in raw.iloc[i] if not _is_nan(c)}
        if _norm_hdr(want["name"]) in cells:
            hits = sum(_norm_hdr(w) in cells for w in want.values() if w)
            if hits > best_hits:
                best_row, best_hits = i, hits
    if best_row is None:
        return None, None, [f'The header text "{want["name"]}" (for `name`) is not found in the first 30 rows. '
                            f'Copy header texts exactly as written.'], []

    header = ["" if _is_nan(c) else _norm_hdr(c) for c in raw.iloc[best_row]]
    problems, warnings_, cols = [], [], {}
    for k, w in want.items():
        if not w:
            cols[k] = None
            continue
        nw = _norm_hdr(w)
        close = [nw] if nw in header else difflib.get_close_matches(nw, [h for h in header if h], n=1, cutoff=0.85)
        cols[k] = header.index(close[0]) if close else None
        if cols[k] is None:
            problems.append(f'The header "{w}" (for `{k}`) is not found in the header row (row {best_row}).')

    body = raw.iloc[best_row + 1: best_row + 201]

    def vals(k):
        return [] if cols[k] is None else [v for v in body.iloc[:, cols[k]] if not _is_nan(v) and str(v).strip()]

    if cols["name"] is not None:
        v = vals("name")
        if not v:
            problems.append(f'The column `name` = "{want["name"]}" is empty below the header.')
        elif sum(map(_is_non_name, v)) / len(v) > 0.5:
            problems.append(f'`name` = "{want["name"]}" contains numbers/dates/durations (e.g. "{v[0]}"), '
                            f'not task names. Choose the column with text names of the tasks.')
    if cols["duration"] is not None:
        v = vals("duration")
        if not v or sum(parse_duration(x, "days")[0] is not None for x in v) / len(v) < 0.5:
            problems.append(f'`duration` = "{want["duration"]}" does not contain durations (e.g. "{v[0] if v else ""}").')
    for k in ("start", "finish"):
        if cols[k] is not None:
            v = vals(k)
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                ok = pd.to_datetime(pd.Series(v, dtype=object), dayfirst=True, errors="coerce").notna().mean() if v else 0
            if ok < 0.5:
                problems.append(f'`{k}` = "{want[k]}" does not contain dates (e.g. "{v[0] if v else ""}").')
    if cols["criticality"] is not None:               # только числа 0–1; остальное молча не используем
        v = vals("criticality")
        num = pd.to_numeric(pd.Series([str(x).replace(",", ".") for x in v]), errors="coerce")
        if not len(v) or not num.between(0, 1).mean() >= 0.5:
            warnings_.append(f'колонка «{want["criticality"]}» не содержит чисел 0–1 и не используется как criticality')
            cols["criticality"] = None
    return best_row, cols, problems, warnings_


def _norm_name(s: str) -> str:
    return re.sub(r"\s+", " ", str(s).strip().lower())


def sanitize_item(it: dict, vocab: Vocab) -> dict | None:
    """Ответ модели — недоверенные данные: проверяем словари, иначе строка будет переспрошена."""
    try:
        canon_map = {k.lower(): k for k in vocab.activities}
        canon = str(it["canonical_activity"]).strip()
        canon = UNMAPPED if canon.upper() == UNMAPPED else canon_map.get(canon.lower())
        kind = it["row_kind"]
        if canon is None or kind not in ROW_KINDS:
            return None
        phase = str(it.get("phase_if_unmapped", "")).strip()
        phase = next((p for p in vocab.phases if p.lower() == phase.lower()), None)
        if kind == "work" and canon == UNMAPPED and phase is None:
            return None            # invalid: unmapped work must get a real phase, never "NONE"/missing
        return {
            "row_id": int(it["row_id"]), "row_kind": kind, "canonical_activity": canon,
            "phase_if_unmapped": phase,
            "proposed_equipment": [e for e in it.get("proposed_equipment", []) if e in EQUIPMENT_CLASSES],
            "confidence": min(1.0, max(0.0, float(it.get("confidence", 0)))),
            "evidence": str(it.get("evidence", ""))[:200],
        }
    except (KeyError, TypeError, ValueError, AttributeError):
        return None


# --------------------------------------------------------------------------- #
# 4. Основной конвейер
# --------------------------------------------------------------------------- #
def normalize(src_path: str, canonical_path: str, project_id: str,
              llm: LLM | None = None, cache_path: str | None = "classification_cache.json",
              sep: str | None = None):
    llm = llm or yandex_llm
    vocab = Vocab(canonical_path)
    raw = read_table(src_path, sep)

    # ---- Этап 1: структура файла (модель называет колонки по ТЕКСТУ заголовков, номера находит код) ----
    preview = raw.head(30).to_csv(index=True, header=False)
    user = ("Raw table fragment (first 30 rows; the left-most value of each line is the row number of the "
            f"file, not a column):\n{preview}")
    feedback, last_err = "", None
    for _ in range(3):
        try:
            s = llm(SYSTEM_SCHEMA, user + feedback, TOOL_SCHEMA)
            header_row, cols, problems, warns = resolve_structure(raw, s["columns"])
            unit_hint = s["duration_unit"] if s.get("duration_unit") in UNIT_KINDS else "unknown"
            if not problems:
                break
            last_err = " ".join(problems)
            feedback = ("\n\nYour previous answer was REJECTED by validation: " + last_err +
                        "\nYour previous answer: " + json.dumps(s["columns"], ensure_ascii=False) +
                        "\nReturn a corrected answer.")
        except (KeyError, TypeError, ValueError, AttributeError) as e:      # JSONDecodeError — подкласс ValueError
            last_err, feedback = repr(e), ""
    else:
        raise RuntimeError(f"Не удалось определить структуру файла: {last_err}")
    for w in warns:
        print("WARNING:", w)

    def _col(idx):
        return "нет" if idx is None else f"{idx} «{raw.iloc[header_row, idx]}»"
    print(f"Структура файла: заголовок в строке {header_row}; единицы длительности: {unit_hint}")
    print("  колонки: " + "; ".join(f"{k} = {_col(v)}" for k, v in cols.items()))

    def get(row, key):
        idx = cols.get(key)
        return None if idx is None else row.iloc[idx]

    # ---- контекст: иерархия и родители ------------------------------------
    records, stack = [], []
    lvl_idx = cols.get("outline_level")
    wbs_mode = lvl_idx is not None and any(
        re.fullmatch(r"\d+(\.\d+)+", str(v).strip()) for v in raw.iloc[header_row + 1:, lvl_idx].dropna())
    for row_id, r in raw.iloc[header_row + 1:].iterrows():   # row_id = номер строки в файле
        name = get(r, "name")
        if _is_nan(name) or not str(name).strip():
            continue
        lvl = _outline_level(get(r, "outline_level"), wbs_mode)
        if lvl is not None:
            while stack and stack[-1][0] >= lvl:
                stack.pop()
        records.append({"row_id": int(row_id), "name": str(name).strip(), "level": lvl,
                        "parents": [n for _, n in stack], "raw": r})
        if lvl is not None:
            stack.append((lvl, str(name).strip()))
    n_levels = len({r["level"] for r in records if r["level"] is not None})
    if n_levels > MAX_OUTLINE_LEVELS:        # колонка «уровень» на деле оказалась нумерацией строк
        print(f"WARNING: в колонке уровней {n_levels} разных значений (это не иерархия) — вложенность игнорируется")
        for rec in records:
            rec["level"], rec["parents"] = None, []
    for i, rec in enumerate(records):        # summary по вложенности — надёжнее мнения LLM
        nxt = records[i + 1]["level"] if i + 1 < len(records) else None
        rec["is_parent"] = rec["level"] is not None and nxt is not None and nxt > rec["level"]
    if records and sum(r["is_parent"] for r in records) > 0.5 * len(records):
        print("WARNING: больше половины строк оказались «заголовками разделов» — вложенность игнорируется")
        for rec in records:
            rec["level"], rec["parents"], rec["is_parent"] = None, [], False
    print(f"Строк с названиями работ: {len(records)}; заголовков разделов по вложенности: "
          f"{sum(r['is_parent'] for r in records)}")
    if not records:
        print(f"WARNING: после строки {header_row} в колонке названий ({_col(cols['name'])}) нет ни одного значения")

    # ---- Этап 2: классификация батчами + кэш решений ----------------------
    cache = json.loads(Path(cache_path).read_text(encoding="utf-8")) if cache_path and Path(cache_path).exists() else {}
    system, tool = build_classify_system(vocab), classify_tool(vocab)

    def key(rec):  # решение зависит от названия и раздела
        return _norm_name(" > ".join(rec["parents"] + [rec["name"]]))

    todo = [r for r in records if key(r) not in cache]
    for attempt in range(3):
        for i in range(0, len(todo), BATCH_SIZE):
            batch = todo[i:i + BATCH_SIZE]
            payload = json.dumps([{"row_id": b["row_id"], "name": b["name"], "parents": b["parents"]}
                                  for b in batch], ensure_ascii=False)
            try:
                out = llm(system, f"Classify these rows:\n{payload}", tool)["items"]
            except (KeyError, TypeError, ValueError):
                continue                                        # битый ответ — батч будет переспрошен
            by_id = {b["row_id"]: b for b in batch}
            for it in out:
                clean = sanitize_item(it, vocab) if isinstance(it, dict) else None
                if clean and clean["row_id"] in by_id:
                    cache[key(by_id[clean["row_id"]])] = clean
        todo = [r for r in todo if key(r) not in cache]     # что LLM потеряла — переспрашиваем
        if not todo:
            break
    kinds = Counter((cache.get(key(r)) or {}).get("row_kind", "нет ответа") for r in records)
    print(f"Классификация LLM (по типам строк): {dict(kinds)}")
    if cache_path:
        Path(cache_path).write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")

    # ---- Сборка канонических строк (только код) ----------------------------
    rows, log, seq = [], [], 0
    for rec in records:
        c = cache.get(key(rec))
        flags: list[str] = []
        if c is None:
            c = {"row_kind": "work", "canonical_activity": UNMAPPED, "phase_if_unmapped": None,
                 "proposed_equipment": [], "confidence": 0.0, "evidence": "LLM не вернула ответ"}
            flags.append("llm_missing")
        if rec["is_parent"] or c["row_kind"] in ("summary", "milestone"):
            log.append({"source_row": rec["row_id"], "source_name": rec["name"], "included": False,
                        "reason": "summary" if rec["is_parent"] else c["row_kind"]})
            continue

        r = rec["raw"]
        dur, f = parse_duration(get(r, "duration"), unit_hint)
        if f: flags.append(f)
        if dur is None and cols["start"] is not None and cols["finish"] is not None:
            a = pd.to_datetime(get(r, "start"), dayfirst=True, errors="coerce")
            b = pd.to_datetime(get(r, "finish"), dayfirst=True, errors="coerce")
            if pd.notna(a) and pd.notna(b):
                dur, flags = float((b - a).days + 1), flags + ["duration_from_calendar_dates"]
        if dur is None:
            flags.append("duration_missing")

        crit = get(r, "criticality")
        try:
            crit = float(str(crit).replace(",", "."))
            if not 0 <= crit <= 1:
                crit, flags = None, flags + ["criticality_out_of_range"]
        except (TypeError, ValueError):
            if not _is_nan(crit):
                flags.append("criticality_categorical_not_converted")   # §11 спецификации
            crit = None
        if crit is None and "criticality_categorical_not_converted" not in flags:
            flags.append("criticality_missing")

        status = get(r, "status")
        if _is_nan(status):
            status, flags = "Planned", flags + ["status_defaulted_to_Planned"]

        canon = c["canonical_activity"]
        if canon != UNMAPPED:
            a = vocab.activities[canon]
            phase, order, equip = a["phase"], a["phase_order"], list(a["equipment"])
            name = canon
        else:
            phase = c["phase_if_unmapped"]
            order = vocab.phases.get(phase)
            equip = [e for e in c["proposed_equipment"] if e in EQUIPMENT_CLASSES]
            name = rec["name"]
            flags += ["activity_unmapped", "equipment_llm_suggested"]
            if phase is None:
                # LLM could not/would not name a phase for this unmapped row (e.g. answered "NONE"
                # instead of guessing). Never fabricate a phase (see module docstring) — drop just this
                # row for manual review instead of failing the whole plan on one ambiguous activity.
                log.append({"source_row": rec["row_id"], "source_name": rec["name"], "included": False,
                            "reason": "phase_unresolved", "evidence": c["evidence"]})
                continue
        if c["confidence"] < REVIEW_THRESHOLD:
            flags.append("low_confidence")

        seq += 1
        src_id = get(r, "id")
        rows.append({
            "project_id": project_id,
            "activity_id": f"{project_id}_{str(src_id).strip()}" if not _is_nan(src_id) else f"{project_id}_A{seq:04d}",
            "phase": phase, "phase_order": order, "activity_sequence": seq,
            "activity_name": name, "planned_duration_days": dur, "criticality": crit,
            "status": status, "expected_equipment": equip,
        })
        log.append({"source_row": rec["row_id"], "source_name": rec["name"], "included": True,
                    "canonical_activity": canon, "confidence": c["confidence"],
                    "evidence": c["evidence"], "flags": ";".join(flags),
                    "needs_review": any(x in flags for x in
                                        ("low_confidence", "activity_unmapped", "duration_missing",
                                         "duration_unit_unsupported", "phase_missing", "llm_missing"))})

    ids = [r["activity_id"] for r in rows]
    if len(set(ids)) != len(ids):                      # коды заказчика не уникальны — заменяем на сгенерированные
        print("WARNING: коды работ в файле не уникальны, activity_id сгенерированы заново")
        for i, r in enumerate(rows, 1):
            r["activity_id"] = f"{project_id}_A{i:04d}"
    if not rows:
        print("ПЛАН ПУСТ. Возможные причины: неверно определены заголовок/колонка названий (см. «Структура файла» "
              "выше), все строки отнесены к заголовкам/вехам, либо в кэше classification_cache.json старые "
              "неверные решения (удалите его). Примеры прочитанных названий: "
              + "; ".join(r["name"] for r in records[:3]))
    plan, log_df = pd.DataFrame(rows, columns=CANON_COLS), pd.DataFrame(log)
    return plan, log_df, validate(plan, vocab), vocab


# --------------------------------------------------------------------------- #
# 5. Валидация по разделу 21 спецификации
# --------------------------------------------------------------------------- #
def validate(plan: pd.DataFrame, vocab: Vocab) -> list[str]:
    errs = []
    if list(plan.columns) != CANON_COLS:
        errs.append("нарушен состав/порядок колонок")
    if plan.empty:
        return errs + ["план пуст"]
    if plan["activity_id"].duplicated().any():
        errs.append("activity_id не уникален")
    for col in ("project_id", "activity_id", "phase", "phase_order", "activity_name", "status"):
        if plan[col].isna().any():
            errs.append(f"пустые значения в обязательном поле {col}")
    if plan["activity_sequence"].tolist() != list(range(1, len(plan) + 1)):
        errs.append("activity_sequence не сплошной")
    if (plan.groupby("phase")["phase_order"].nunique() > 1).any():
        errs.append("phase и phase_order не согласованы")
    if plan["criticality"].dropna().between(0, 1).eq(False).any():
        errs.append("criticality вне диапазона 0–1")
    if plan["expected_equipment"].map(lambda l: any(e not in EQUIPMENT_CLASSES for e in l)).any():
        errs.append("expected_equipment содержит класс вне словаря из 13")
    if (plan["phase_order"].dropna().diff().dropna() < 0).any():
        errs.append("WARNING: фазы идут не по возрастанию — проверьте порядок работ в исходнике")
    for col in ("planned_duration_days", "criticality"):
        n = plan[col].isna().sum()
        if n:
            errs.append(f"WARNING: {col} не заполнено в {n} строках (данных нет в источнике, не выдумываем)")
    return errs


# --------------------------------------------------------------------------- #
# 6. Экспорт и точки входа
# --------------------------------------------------------------------------- #
def export(plan, log_df, vocab, out_dir="data/plan_normalized", xlsx=False) -> Path:
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    out = plan.copy()
    out["expected_equipment"] = out["expected_equipment"].map(str)   # "['worker', ...]" как в эталоне
    out.to_csv(d / "activities_normalized.csv", index=False, encoding="utf-8")   # для модулей 3 и 4
    log_df.to_csv(d / "normalization_log.csv", index=False, encoding="utf-8-sig")  # BOM — чтобы Excel читал кириллицу
    if xlsx:
        with pd.ExcelWriter(d / "plan_normalized.xlsx") as w:
            out.to_excel(w, sheet_name="plan", index=False)
            vocab.equip_desc.to_excel(w, sheet_name="equipment_descriptions", index=False)
            log_df.to_excel(w, sheet_name="normalization_log", index=False)
    return d


def run(src: str, vocab_path: str, project_id: str, out_dir: str = "data/plan_normalized",
        xlsx: bool = False, sep: str | None = None, llm: LLM | None = None):
    """Один вызов: прочитать план заказчика, нормализовать, сохранить. -> (plan, log, problems)."""
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    plan, log_df, problems, vocab = normalize(
        src, vocab_path, project_id, llm=llm, sep=sep,
        cache_path=str(Path(out_dir) / "classification_cache.json"))
    export(plan, log_df, vocab, out_dir, xlsx)
    return plan, log_df, problems


def _abs(p: str) -> str:
    return p if Path(p).is_absolute() else str(BASE_DIR / p)


def main(argv=None) -> int:
    """Без аргументов берёт блок НАСТРОЙКИ; аргументы командной строки (если есть) его переопределяют."""
    ap = argparse.ArgumentParser(description="Модуль 1: план заказчика -> канонический формат")
    ap.add_argument("src", nargs="?", default=_abs(SRC_PATH), help="план заказчика (.csv или .xlsx)")
    ap.add_argument("--vocab", default=_abs(VOCAB_PATH), help="эталонный канонический план (.csv/.xlsx)")
    ap.add_argument("--project-id", default=PROJECT_ID)
    ap.add_argument("--out-dir", default=_abs(OUT_DIR))
    ap.add_argument("--sep", default=CSV_SEP, help="разделитель CSV (по умолчанию определяется сам)")
    ap.add_argument("--xlsx", action="store_true", default=SAVE_XLSX, help="дополнительно сохранить .xlsx")
    a = ap.parse_args(argv)

    key = os.environ.get("YANDEX_API_KEY", "")
    print(f"Вход: {a.src}\nЭталон: {a.vocab}\nProject ID: {a.project_id}\n"
          f"Ключ: {key[:4] + '…' + key[-4:] if len(key) > 8 else 'НЕ ЗАДАН'} | "
          f"каталог: {os.environ.get('YANDEX_FOLDER_ID') or 'НЕ ЗАДАН'}")
    plan, log_df, problems = run(a.src, a.vocab, a.project_id, a.out_dir, a.xlsx, a.sep)
    n_review = int(log_df["needs_review"].fillna(False).sum()) if "needs_review" in log_df else 0
    print(f"строк в плане: {len(plan)}, на проверку эксперту: {n_review}, результат: {a.out_dir}")
    print("\n".join(problems) or "валидация пройдена")
    return 1 if any(not p.startswith("WARNING") for p in problems) else 0


if __name__ == "__main__":
    sys.exit(main())
