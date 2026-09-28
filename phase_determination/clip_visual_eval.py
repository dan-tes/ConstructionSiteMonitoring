"""Другие визуальные бэкбоны для фазы по снимку: CLIP / SigLIP (open_clip).

Сравнение на тех же кадрах таймлапсов, что и visual_phase_timelapse.py
(DINOv2 из services/visual_phase + линейная голова, LOVO 50.6% по кадрам):
  - zero-shot: без обучения, по текстовым описаниям 10 фаз (знает и
    Preconstruction, которой нет в роликах);
  - linear probe: логистическая регрессия поверх эмбеддингов, LOVO;
  - вклад в ансамбль: добавка к (техника + DINOv2) по дням, как в
    ensemble_eval.py.

Запуск: ~/.venvs/csm-gpu/bin/python phase_determination/clip_visual_eval.py
Выход: timelapse_eval/visual/clip_summary.md, кэш эмбеддингов emb_*.npz
"""
from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import torch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "phase_determination"))
import timelapse_phase_eval as tpe  # noqa: E402
import visual_phase_timelapse as vpt  # noqa: E402

BACKBONES = [("ViT-B-16-SigLIP", "webli"), ("ViT-L-14", "laion2b_s32b_b82k")]
PHASE_PROMPTS = {
    "Preconstruction": "an empty plot of land before construction starts",
    "Site Preparation": "site clearing and levelling, with fences and site cabins being set up",
    "Earthwork": "excavation of a building pit with excavators and dump trucks",
    "Foundation": "foundation works in an excavated pit with rebar, formwork and a concrete foundation slab",
    "Structural Frame": "the concrete or steel frame of a building rising floor by floor under tower cranes",
    "Masonry": "brick and block walls being laid on a building shell",
    "MEP": "installation of pipes, ducts and electrical systems inside an unfinished building",
    "Finishing": "facade cladding, windows and exterior finishing on a nearly complete building with scaffolding",
    "External Works": "landscaping, paving and road works around a completed building",
    "Commissioning": "a completed new building ready for handover",
}
TEMPLATES = ["a photo of a construction site: {}.", "a time-lapse frame of {}.", "{}"]
EPS = 1e-6


def embed_frames(name: str, pretrained: str) -> tuple[dict, object, object]:
    import open_clip  # noqa: PLC0415

    device = "cuda" if torch.cuda.is_available() else "cpu"
    model, _, preprocess = open_clip.create_model_and_transforms(name, pretrained=pretrained, device=device)
    model.eval()
    tokenizer = open_clip.get_tokenizer(name)

    @torch.no_grad()
    def embed(x):
        f = model.encode_image(x.to(device))
        return torch.nn.functional.normalize(f, dim=-1).float().cpu().numpy()

    cache = vpt.OUT / f"emb_{name}_{pretrained}.npz"
    data = vpt.extract_embeddings(cache=cache, transform=preprocess, embed=embed)
    return data, model, tokenizer


@torch.no_grad()
def zero_shot(model, tokenizer, emb: np.ndarray, names: list[str]) -> np.ndarray:
    device = next(model.parameters()).device
    classes = []
    for p in names:
        tok = tokenizer([t.format(PHASE_PROMPTS[p]) for t in TEMPLATES]).to(device)
        f = torch.nn.functional.normalize(model.encode_text(tok), dim=-1).mean(0)
        classes.append(torch.nn.functional.normalize(f, dim=0))
    text = torch.stack(classes).float().cpu().numpy()
    logits = 100.0 * emb @ text.T
    p = np.exp(logits - logits.max(1, keepdims=True))
    return p / p.sum(1, keepdims=True)


def probe_lovo(emb, y, videos, n) -> np.ndarray:
    out = np.zeros((len(y), n))
    lab = y >= 0
    for v in np.unique(videos):
        tr = (videos != v) & lab
        scaler, clf = vpt.fit_head(emb[tr], y[tr], n)
        out[videos == v] = vpt.head_probs(scaler, clf, emb[videos == v], n)
    return out


def to_days(frame_probs, frame_video, frame_t, day_video, day_t):
    frames = defaultdict(list)
    for i, (v, t) in enumerate(zip(frame_video, frame_t)):
        frames[v].append((t, i))
    out = np.full((len(day_video), frame_probs.shape[1]), np.nan)
    for v in frames:
        frames[v].sort()
        ts = np.array([t for t, _ in frames[v]])
        ids = np.array([i for _, i in frames[v]])
        m = day_video == v
        k = np.searchsorted(ts, day_t[m], side="right") - 1
        rows = np.flatnonzero(m)
        ok = k >= 0
        out[rows[ok]] = frame_probs[ids[k[ok]]]
    return out


def loglin(ws, ms):
    s = sum(w * np.log(m + EPS) for w, m in zip(ws, ms))
    e = np.exp(s - s.max(1, keepdims=True))
    return e / e.sum(1, keepdims=True)


def main() -> None:
    names = vpt.phase_names()
    pid = {p: i for i, p in enumerate(names)}
    tpe._set_out_dir(ROOT / "phase_determination" / "timelapse_eval")
    marks = tpe._read_labels(names)

    # опорные участники по дням: техника (LOVO) и DINOv2-голова (LOVO)
    eq = np.load(ROOT / "phase_determination" / "timelapse_eval" / "finetune_lovo" / "probs.npz")
    day_video, day_t, eq_p = eq["video"], eq["t_s"], eq["probs"]
    truth = np.array([pid[tpe._label_at(marks[v], t)] for v, t in zip(day_video, day_t)])
    dino = vpt.extract_embeddings()
    dino_y = np.array([pid.get(tpe._label_at(marks[v], t), -1) for v, t in zip(dino["video"], dino["t_s"])])
    dino_frame = probe_lovo(dino["emb"], dino_y, dino["video"], len(names))
    dino_day = to_days(dino_frame, dino["video"], dino["t_s"], day_video, day_t)

    lines = ["# CLIP / SigLIP для фазы по снимку (кадры таймлапсов, LOVO)", "",
             f"Опора: DINOv2-голова — кадры {(dino_frame.argmax(1) == dino_y)[dino_y >= 0].mean():.1%}.", ""]
    extra_members = {}
    for name, pretrained in BACKBONES:
        data, model, tok = embed_frames(name, pretrained)
        assert (data["video"] == dino["video"]).all() and np.allclose(data["t_s"], dino["t_s"]), "кадры не совпали"
        zs = zero_shot(model, tok, data["emb"], names)
        pr = probe_lovo(data["emb"], dino_y, data["video"], len(names))
        lab = dino_y >= 0
        lines.append(f"## {name} ({pretrained})")
        lines.append(f"- zero-shot по кадрам: {(zs.argmax(1) == dino_y)[lab].mean():.1%}")
        lines.append(f"- linear probe LOVO по кадрам: {(pr.argmax(1) == dino_y)[lab].mean():.1%}")
        extra_members[f"{name} zero-shot"] = to_days(zs, data["video"], data["t_s"], day_video, day_t)
        extra_members[f"{name} probe"] = to_days(pr, data["video"], data["t_s"], day_video, day_t)
        lines.append("")
        del model
        torch.cuda.empty_cache()

    ok = ~np.isnan(dino_day).any(1)
    for m in extra_members.values():
        ok &= ~np.isnan(m).any(1)

    def acc(p):
        pred = p.argmax(1)
        return (pred == truth[ok]).mean(), np.mean([(pred[truth[ok] == c] == c).mean() for c in np.unique(truth[ok])])

    lines += [f"## Ансамбль по дням ({ok.sum()} дней, детекции прод-YOLO)", "",
              "| вариант | точность | средняя по фазам |", "|---|---|---|"]
    base = loglin([0.6, 0.4], [eq_p[ok], dino_day[ok]])
    lines.append("| техника + DINOv2 (сейчас в проде, без 2-й модели техники) | %.1f%% | %.1f%% |" % tuple(100 * x for x in acc(base)))
    for label, m in extra_members.items():
        lines.append(f"| {label} отдельно | %.1f%% | %.1f%% |" % tuple(100 * x for x in acc(m[ok])))
        for w in (0.2, 0.3):
            f = loglin([0.6 * (1 - w), 0.4 * (1 - w), w], [eq_p[ok], dino_day[ok], m[ok]])
            lines.append(f"| техника + DINOv2 + {label} (w={w}) | %.1f%% | %.1f%% |" % tuple(100 * x for x in acc(f)))
    out = vpt.OUT / "clip_summary.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
