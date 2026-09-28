"""Итоговый классификатор services/visual_phase — ансамбль трёх визуальных
участников, обученный на ВСЕХ 60 размеченных стройках (data/*.mp4 + data/yt):
  1. DINOv2 (services/visual_phase/weights/backbone_best.pt) + логрегрессия;
  2. SigLIP ViT-B-16 (open_clip, webli) + логрегрессия;
  3. SigLIP zero-shot по текстовым описаниям фаз (clip_visual_eval.PHASE_PROMPTS;
     текстовые эмбеддинги посчитаны здесь, токенайзер в проде не нужен).
Объединение log-линейное, веса поровну. Held-out оценка этой схемы —
eval_all.py (timelapse_eval_all/summary.md): вместе с модулем 3 ~66% по дням
против 60.6% без SigLIP.

Запуск: ~/.venvs/csm-gpu/bin/python phase_determination/build_visual_ensemble.py
Выход: services/visual_phase/weights/classifier.json ("type": "ensemble")
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import torch

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "services" / "phase"), str(ROOT / "phase_determination")]
import clip_visual_eval as cve  # noqa: E402
import eval_all as ea  # noqa: E402
import visual_phase_timelapse as vpt  # noqa: E402

OUT = ROOT / "services" / "visual_phase" / "weights" / "classifier.json"


def head_json(emb, y, names):
    scaler, clf = vpt.fit_head(emb[y >= 0], y[y >= 0], len(names))
    return {
        "classes": [names[c] for c in clf.classes_],
        "scaler_mean": scaler.mean_.tolist(),
        "scaler_scale": scaler.scale_.tolist(),
        "coef": clf.coef_.tolist(),
        "intercept": clf.intercept_.tolist(),
    }


@torch.no_grad()
def text_embeddings(names):
    m = ea._siglip_model()
    rows = []
    for p in names:
        tok = m["tokenizer"]([t.format(cve.PHASE_PROMPTS[p]) for t in cve.TEMPLATES]).to(m["device"])
        f = torch.nn.functional.normalize(m["model"].encode_text(tok), dim=-1).mean(0)
        rows.append(torch.nn.functional.normalize(f, dim=0).float().cpu().numpy().tolist())
    return rows


def main() -> None:
    names = vpt.phase_names()
    dino, siglip = ea.embeddings("dino"), ea.embeddings("siglip")
    assert (dino["video"] == siglip["video"]).all()
    y = ea.labels_for(dino["video"], dino["t_s"], names)
    name, pretrained = cve.BACKBONES[0]
    videos = sorted(set(dino["video"][y >= 0].tolist()))
    classifier = {
        "type": "ensemble",
        "phase_names": names,
        "siglip": {"name": name, "pretrained": pretrained},
        "members": [
            {"backbone": "dino", "weight": 1 / 3, "head": head_json(dino["emb"], y, names)},
            {"backbone": "siglip", "weight": 1 / 3, "head": head_json(siglip["emb"], y, names)},
            {"backbone": "siglip", "weight": 1 / 3,
             "zero_shot": {"classes": names, "text_embeddings": text_embeddings(names), "logit_scale": 100.0}},
        ],
        "trained_on": videos,
        "trained_frames": int((y >= 0).sum()),
    }
    OUT.write_text(json.dumps(classifier), encoding="utf-8")
    print(f"{OUT}: {len(videos)} строек, {classifier['trained_frames']} кадров, {OUT.stat().st_size // 1024} KB")


if __name__ == "__main__":
    main()
