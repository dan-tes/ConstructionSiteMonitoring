"""Какой ансамбль фаз подключать в бэкенд — сравнение на held-out данных.

Все участники дают вероятности по дням, которые НЕ видели ролик, на котором
их проверяют:
  - eq_ft  — модуль 3 v2 + дообучение на таймлапсах, leave-one-video-out
             (finetune_timelapse.py -> finetune_lovo/probs.npz);
  - eq_v2  — модуль 3 v2 без таймлапсов (синтетика + симулятор детектора),
             held-out по построению;
  - vis    — линейная голова поверх DINOv2 (visual_phase_timelapse.py),
             leave-one-video-out; на день берётся последний кадр не позже дня.

Варианты объединения:
  mean_w   — log p = sum_i w_i log p_i (веса по сетке);
  stack    — логистическая регрессия поверх log p всех участников,
             ВЛОЖЕННЫЙ leave-one-video-out (объединитель для ролика V учится
             только на днях других роликов);
  +filter  — учёт истории проекта: причинный forward-фильтр по снимкам
             (дни с кадром) с монотонной матрицей переходов: фаза держится
             или идёт вперёд, назад — с малой вероятностью. Так же сможет
             работать бэкенд: у него есть предыдущие записи проекта.
Метрика — точность по размеченным дням + средняя по фазам, детекции
текущей прод-YOLO (new_yolo) и старой (old_yolo).

Запуск: ~/.venvs/csm-gpu/bin/python phase_determination/ensemble_eval.py
"""
from __future__ import annotations

import sys
from collections import defaultdict
from datetime import timedelta
from pathlib import Path

import numpy as np
import torch

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "services" / "phase"), str(ROOT / "phase_determination")]
import finetune_timelapse as ft  # noqa: E402
import timelapse_phase_eval as tpe  # noqa: E402
import train_phase_v2 as t2  # noqa: E402
import visual_phase_timelapse as vpt  # noqa: E402
from features import FEATURE_VERSION, build_phase_meta, build_phase_structured, build_window  # noqa: E402

EPS = 1e-6


def transition_matrix(n: int, stay: float, fwd: float, back: float) -> np.ndarray:
    """Строки — из какой фазы, столбцы — в какую. Шаг — от снимка к снимку."""
    A = np.zeros((n, n))
    for i in range(n):
        A[i, i] = stay
        for j in range(i + 1, n):
            A[i, j] = fwd * 0.5 ** (j - i - 1)  # дальше вперёд — реже
        for j in range(i):
            A[i, j] = back * 0.5 ** (i - j - 1)
        A[i] /= A[i].sum()
    return A


def forward_filter(emis: np.ndarray, steps: np.ndarray, A: np.ndarray) -> np.ndarray:
    """emis: (T, P) вероятности на днях с новым снимком, по порядку; steps:
    (T,) 1, если в этот день пришёл новый снимок (иначе день просто
    наследует состояние). Возвращает отфильтрованные p(фаза | снимки до дня)."""
    out = np.zeros_like(emis)
    belief = np.full(emis.shape[1], 1.0 / emis.shape[1])
    for t in range(len(emis)):
        if steps[t]:
            belief = (belief @ A) * emis[t]
            belief /= belief.sum()
        out[t] = belief
    return out


def v2_base_probs(series, days_index, phase_names, classes, checkpoint=None):
    """Вероятности чекпоинта (по умолчанию eq_v2) на тех же днях, что и LOVO-вероятности."""
    base = torch.load(checkpoint or t2.DATA_DIR / "construction_phase_checkpoints_v2" / "best.pt", map_location="cpu", weights_only=False)
    model = ft.new_model(base, base["config"]).eval()
    from sentence_transformers import SentenceTransformer  # noqa: PLC0415

    _, _, prevalence, meta = build_phase_meta(t2.DATA_DIR)
    text = SentenceTransformer(t2.TEXT_MODEL_NAME, device="cpu").encode(
        meta["phase_text"].tolist(), convert_to_tensor=True, normalize_embeddings=True
    ).float().unsqueeze(0).to(t2.DEVICE)
    structured = torch.from_numpy(build_phase_structured(phase_names, prevalence, meta)).unsqueeze(0).to(t2.DEVICE)
    out = np.zeros((len(days_index), len(phase_names)))
    by_video = defaultdict(list)
    for i, (v, d) in enumerate(days_index):
        d = int(d)
        by_video[v].append((i, d))
    W = base["config"]["window_size"]
    with torch.no_grad():
        for v, items in by_video.items():
            hist = {ft.BASE_DATE + timedelta(days=d): c for d, c in series[v]["counts"].items()}
            for k in range(0, len(items), 256):
                chunk = items[k : k + 256]
                obs = torch.from_numpy(np.stack([
                    build_window(hist, ft.BASE_DATE + timedelta(days=d), W, classes, FEATURE_VERSION) for _, d in chunk
                ])).to(t2.DEVICE)
                e = model(obs, text.expand(len(chunk), -1, -1), structured.expand(len(chunk), -1, -1)).emissions[:, -1]
                out[[i for i, _ in chunk]] = torch.softmax(e, -1).cpu().numpy()
    return out


def visual_day_probs(eq_videos, eq_t, names):
    data = vpt.extract_embeddings()
    tpe._set_out_dir(ROOT / "phase_determination" / "timelapse_eval")
    marks = tpe._read_labels(names)
    pid = {p: i for i, p in enumerate(names)}
    y = np.array([pid.get(tpe._label_at(marks[v], t), -1) for v, t in zip(data["video"], data["t_s"])])
    V, T, X = data["video"], data["t_s"], data["emb"]
    frame_p = np.zeros((len(y), len(names)))
    for v in sorted(set(V)):
        tr = (V != v) & (y >= 0)
        scaler, clf = vpt.fit_head(X[tr], y[tr], len(names))
        frame_p[V == v] = vpt.head_probs(scaler, clf, X[V == v], len(names))
    frames = defaultdict(list)
    for i, (v, t) in enumerate(zip(V, T)):
        frames[v].append((t, i))
    for v in frames:
        frames[v].sort()
    out = np.zeros((len(eq_videos), len(names)))
    has = np.zeros(len(eq_videos), bool)
    for k, (v, t) in enumerate(zip(eq_videos, eq_t)):
        idx = [i for ft_, i in frames[v] if ft_ <= t]
        if idx:
            out[k], has[k] = frame_p[idx[-1]], True
    return out, has


def stack_lovo(members: list[np.ndarray], truth, videos, n):
    from sklearn.linear_model import LogisticRegression  # noqa: PLC0415

    F = np.concatenate([np.log(m + EPS) for m in members], axis=1)
    out = np.zeros((len(truth), n))
    for v in np.unique(videos):
        tr = videos != v
        clf = LogisticRegression(C=0.1, max_iter=3000, class_weight="balanced").fit(F[tr], truth[tr])
        p = np.full((int((~tr).sum()), n), EPS)
        p[:, clf.classes_] = clf.predict_proba(F[~tr])
        out[~tr] = p / p.sum(1, keepdims=True)
    return out


def scores(p, truth):
    pred = p.argmax(1)
    return (pred == truth).mean(), np.mean([(pred[truth == c] == c).mean() for c in np.unique(truth)])


def main() -> None:
    names, classes, _, _ = build_phase_meta(t2.DATA_DIR)
    pid = {p: i for i, p in enumerate(names)}
    lines = ["# Ансамбль фаз — held-out сравнение", ""]
    for det, folder in ft.DETECTION_SETS.items():
        z = np.load(folder / "finetune_lovo" / "probs.npz")
        tpe._set_out_dir(folder)
        series = tpe.load_series(names)
        videos, days, t_s, eq_ft = z["video"], z["day"].astype(int).tolist(), z["t_s"], z["probs"]
        days = np.array(days, dtype=object)
        truth = np.array([pid[series[v]["labels"][d]] for v, d in zip(videos, days)])
        eq_v2 = v2_base_probs(series, list(zip(videos, days)), names, classes)
        vis, has_vis = visual_day_probs(videos, t_s, names)
        keep = has_vis
        videos, days, truth, eq_ft, eq_v2, vis = videos[keep], days[keep], truth[keep], eq_ft[keep], eq_v2[keep], vis[keep]
        # новый снимок = в этот день был кадр (иначе прод не получил бы запись)
        step = np.array([d in series[v]["counts"] for v, d in zip(videos, days)])

        def loglin(ws, ms):
            s = sum(w * np.log(m + EPS) for w, m in zip(ws, ms))
            e = np.exp(s - s.max(1, keepdims=True))
            return e / e.sum(1, keepdims=True)

        cands = {
            "eq_ft": eq_ft,
            "eq_v2": eq_v2,
            "vis": vis,
            "eq_ft+vis (0.5/0.5)": loglin([0.5, 0.5], [eq_ft, vis]),
            "eq_ft+eq_v2+vis (1/3 каждому)": loglin([1 / 3] * 3, [eq_ft, eq_v2, vis]),
            "eq_ft+eq_v2+vis (0.4/0.2/0.4)": loglin([0.4, 0.2, 0.4], [eq_ft, eq_v2, vis]),
            "stack(eq_ft, eq_v2, vis)": stack_lovo([eq_ft, eq_v2, vis], truth, videos, len(names)),
        }

        def filtered(p, A):
            out = np.zeros_like(p)
            for v in np.unique(videos):
                m = videos == v
                order = np.argsort(days[m].astype(int))
                idx = np.flatnonzero(m)[order]
                out[idx] = forward_filter(p[idx], step[idx], A)
            return out

        lines += [f"## Детекции {det}: {len(truth)} дней", "", "| вариант | точность | средняя по фазам |", "|---|---|---|"]
        for name, p in cands.items():
            a, b = scores(p, truth)
            lines.append(f"| {name} | {a:.1%} | {b:.1%} |")
        lines += ["", "С учётом истории (forward-фильтр по снимкам; stay / fwd / back):", "",
                  "| вариант | переходы | точность | средняя по фазам |", "|---|---|---|---|"]
        for name in ("eq_ft", "eq_ft+vis (0.5/0.5)", "eq_ft+eq_v2+vis (0.4/0.2/0.4)", "stack(eq_ft, eq_v2, vis)"):
            for stay, fwd, back in [(0.9, 0.1, 0.01), (0.97, 0.03, 0.003), (0.99, 0.01, 0.001)]:
                a, b = scores(filtered(cands[name], transition_matrix(len(names), stay, fwd, back)), truth)
                lines.append(f"| {name} | {stay}/{fwd}/{back} | {a:.1%} | {b:.1%} |")
        lines.append("")
        print("\n".join(lines[-40:]), flush=True)

    out = ROOT / "phase_determination" / "timelapse_eval" / "ensemble_summary.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"записано: {out}")


if __name__ == "__main__":
    main()
