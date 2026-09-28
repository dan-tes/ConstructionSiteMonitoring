"""Held-out оценка всего ансамбля фаз на объединённом наборе таймлапсов
(14 роликов data/*.mp4 + 46 роликов data/yt/*.mp4, разметка — вручную по
контактным листам, см. timelapse_eval*/phase_labels.csv и
timelapse_eval/contact_yt/labels_manual.py).

Участники (все — на роликах, которых не видели при обучении):
  eq_ft   — модуль 3, дообученный на таймлапсах, k-fold по роликам
            (finetune_timelapse.py с FT_WITH_YT=1 FT_FOLDS=N FT_REPORT_ROOT=...);
  eq_v2   — модуль 3 без таймлапсов (синтетика), held-out по построению;
  dino    — DINOv2 (services/visual_phase) + линейная голова, те же фолды;
  siglip  — SigLIP ViT-B-16 + линейная голова, те же фолды;
  siglip0 — SigLIP zero-shot по текстовым описаниям фаз (без обучения).
На день берётся последний кадр не позже дня (как «последний снимок» в проде).

Запуск: ~/.venvs/csm-gpu/bin/python phase_determination/eval_all.py
Выход: timelapse_eval_all/summary.md
"""
from __future__ import annotations

import itertools
import os
import sys
from collections import defaultdict
from datetime import timedelta
from pathlib import Path

import numpy as np
import torch

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "services" / "phase"), str(ROOT / "phase_determination")]
import clip_visual_eval as cve  # noqa: E402
import ensemble_eval as ee  # noqa: E402
import finetune_timelapse as ft  # noqa: E402
import timelapse_phase_eval as tpe  # noqa: E402
import train_phase_v2 as t2  # noqa: E402
import visual_phase_timelapse as vpt  # noqa: E402
from features import build_phase_meta  # noqa: E402

PD = ROOT / "phase_determination"
OUT = PD / "timelapse_eval_all"
# набор роликов: папка с videos.csv/phase_labels.csv -> папка с mp4
SETS = [(PD / "timelapse_eval", ROOT / "data"), (PD / "timelapse_eval_yt", ROOT / "data" / "yt")]
FOLDS = int(os.environ.get("FT_FOLDS", 10))
EPS = 1e-6


def embeddings(kind: str) -> dict:
    """Кадры обоих наборов одним массивом; кэш по набору."""
    parts = []
    for eval_dir, videos_dir in SETS:
        if kind == "dino":
            cache = eval_dir / "visual" / "embeddings.npz"
            parts.append(vpt.extract_embeddings(cache=cache, eval_dir=eval_dir, videos_dir=videos_dir))
        else:
            parts.append(_siglip_embeddings(eval_dir, videos_dir))
    return {k: np.concatenate([p[k] for p in parts]) for k in ("video", "t_s", "emb")}


_siglip = {}


def _siglip_model():
    if not _siglip:
        import open_clip  # noqa: PLC0415

        name, pretrained = cve.BACKBONES[0]
        device = "cuda" if torch.cuda.is_available() else "cpu"
        model, _, preprocess = open_clip.create_model_and_transforms(name, pretrained=pretrained, device=device)
        _siglip.update(model=model.eval(), preprocess=preprocess, tokenizer=open_clip.get_tokenizer(name), device=device)
    return _siglip


def _siglip_embeddings(eval_dir: Path, videos_dir: Path) -> dict:
    name, pretrained = cve.BACKBONES[0]
    cache = eval_dir / "visual" / f"emb_{name}_{pretrained}.npz"
    if cache.exists():
        return vpt.extract_embeddings(cache=cache)
    m = _siglip_model()

    @torch.no_grad()
    def embed(x):
        return torch.nn.functional.normalize(m["model"].encode_image(x.to(m["device"])), dim=-1).float().cpu().numpy()

    return vpt.extract_embeddings(cache=cache, transform=m["preprocess"], embed=embed, eval_dir=eval_dir, videos_dir=videos_dir)


def labels_for(videos, times, names):
    pid = {p: i for i, p in enumerate(names)}
    marks = {}
    for eval_dir, _ in SETS:
        tpe._set_out_dir(eval_dir)
        marks.update(tpe._read_labels(names))
    return np.array([pid.get(tpe._label_at(marks[v], t), -1) if v in marks else -1 for v, t in zip(videos, times)])


def fold_of(videos_sorted: list[str]) -> dict[str, int]:
    """Те же группы, что в finetune_timelapse.py при FT_FOLDS=FOLDS."""
    shuffled = list(np.random.default_rng(t2.SEED).permutation(videos_sorted))
    return {v: i % FOLDS for i, v in enumerate(shuffled)}


def head_cv(emb, y, videos, folds, n):
    out = np.zeros((len(y), n))
    for f in sorted(set(folds.values())):
        te = np.array([folds.get(v, -1) == f for v in videos])
        tr = ~te & (y >= 0) & np.array([v in folds for v in videos])
        scaler, clf = vpt.fit_head(emb[tr], y[tr], n)
        out[te] = vpt.head_probs(scaler, clf, emb[te], n)
    return out


def loglin(ws, ms):
    s = sum(w * np.log(m + EPS) for w, m in zip(ws, ms))
    e = np.exp(s - s.max(1, keepdims=True))
    return e / e.sum(1, keepdims=True)


def main() -> None:
    names, classes, _, _ = build_phase_meta(t2.DATA_DIR)
    pid = {p: i for i, p in enumerate(names)}
    lines = ["# Ансамбль фаз — held-out на объединённом наборе таймлапсов", ""]

    for det in ("new_yolo", "old_yolo"):
        z = np.load(OUT / det / "finetune_lovo" / "probs.npz")
        videos, days, t_s, eq_ft = z["video"], z["day"].astype(int), z["t_s"], z["probs"]
        all_videos = sorted(set(videos.tolist()))
        folds = fold_of(all_videos)

        series = {}
        for eval_dir, _ in SETS:
            base = {PD / "timelapse_eval": PD / "timelapse_eval_old_yolo", PD / "timelapse_eval_yt": PD / "timelapse_eval_yt_old_yolo"}[eval_dir] if det == "old_yolo" else eval_dir
            tpe._set_out_dir(base)
            series.update(tpe.load_series(names))
        truth = np.array([pid[series[v]["labels"][d]] for v, d in zip(videos, days)])
        eq_v2 = ee.v2_base_probs(series, list(zip(videos, days)), names, classes)

        members = {"eq_ft": eq_ft, "eq_v2": eq_v2}
        frames = {}
        for kind in ("dino", "siglip"):
            e = embeddings(kind)
            if "video" not in frames:
                frames = {"video": e["video"], "t_s": e["t_s"]}
                y = labels_for(e["video"], e["t_s"], names)
            assert (e["video"] == frames["video"]).all(), "кадры dino/siglip не совпали"
            fp = head_cv(e["emb"], y, e["video"], folds, len(names))
            members[kind] = cve.to_days(fp, e["video"], e["t_s"], videos, t_s)
            if kind == "siglip":
                m = _siglip_model()
                zs = cve.zero_shot(m["model"], m["tokenizer"], e["emb"], names)
                members["siglip0"] = cve.to_days(zs, e["video"], e["t_s"], videos, t_s)
            frame_acc = (fp.argmax(1) == y)[y >= 0].mean()
            lines.append(f"- {det}: {kind} по кадрам (held-out) {frame_acc:.1%}") if det == "new_yolo" else None

        ok = np.all([~np.isnan(m).any(1) for m in members.values()], axis=0)
        tr = truth[ok]

        def acc(p):
            pred = p.argmax(1)
            return (pred == tr).mean(), np.mean([(pred[tr == c] == c).mean() for c in np.unique(tr)])

        lines += ["", f"## Детекции {det}: {ok.sum()} дней, {len(all_videos)} строек", "",
                  "| вариант | точность | средняя по фазам |", "|---|---|---|"]
        for k, m in members.items():
            lines.append(f"| {k} | %.1f%% | %.1f%% |" % tuple(100 * x for x in acc(m[ok])))
        results = []
        keys = list(members)
        grid = [0.0, 0.1, 0.2, 0.3, 0.4, 0.5]
        for ws in itertools.product(grid, repeat=len(keys)):
            if abs(sum(ws) - 1) > 1e-9 or ws[0] == 0:
                continue
            results.append((acc(loglin(ws, [members[k][ok] for k in keys])), ws))
        results.sort(key=lambda r: -r[0][0])
        prod = acc(loglin([0.4, 0.2, 0.4, 0, 0], [members[k][ok] for k in keys]))
        lines.append("| **как в проде: eq_ft 0.4 / eq_v2 0.2 / dino 0.4** | **%.1f%%** | %.1f%% |" % (100 * prod[0], 100 * prod[1]))
        lines += ["", "Лучшие веса по сетке (оптимистично — подобраны на тех же днях):", ""]
        for (a, b), ws in results[:5]:
            lines.append(f"- {dict(zip(keys, ws))}: {a:.1%} / {b:.1%}")
        best_ws = results[0][1]
        pred = loglin(best_ws, [members[k][ok] for k in keys]).argmax(1)
        lines += ["", "Recall по фазам (лучшие веса):", ""]
        for c in np.unique(tr):
            lines.append(f"- {names[c]}: {(pred[tr == c] == c).mean():.0%} из {(tr == c).sum()} дней")
        lines.append("")
        print("\n".join(lines), flush=True)

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
