"""Визуальный сигнал фазы по кадрам таймлапсов + объединение с модулем 3.

Зачем: модуль 3 смотрит только на технику, а на поздних фазах (каркас ->
кладка -> отделка) на площадке месяцами одно и то же — башенный кран и
рабочие; по бенчмарку (timelapse_eval*/v2, finetune_lovo) отделку он путает
с кладкой. На самом снимке эти фазы видны (леса, фасад, остекление).

services/visual_phase сейчас: DINOv2 (SimSiam-дообученный) + 8 k-means
кластеров, размеченных вручную -> умеет только 4 фазы из 10. Здесь поверх
ТОГО ЖЕ бэкбона обучается линейная голова (логистическая регрессия) на
кадрах таймлапсов с разметкой фаз (phase_labels.csv) — проверка
leave-one-video-out, как у finetune_timelapse.py.

Шаги:
  1. кадры 2/сек в размеченной части ролика -> эмбеддинги бэкбона
     services/visual_phase (GPU), кэш timelapse_eval/visual/embeddings.npz;
  2. LOVO: голова учится на 13 роликах, предсказывает кадры 14-го; для
     каждого дня берётся последний кадр не позже этого дня (как «последний
     загруженный снимок» в проде);
  3. объединение с LOVO-вероятностями модуля 3 (finetune_lovo/probs.npz):
     log p = w * log p_техника + (1 - w) * log p_визуально, по сетке w.
Итог: timelapse_eval/visual/summary.md; голова на всех роликах ->
timelapse_eval/visual/linear_head.json (формат для services/visual_phase).

Запуск: ~/.venvs/csm-gpu/bin/python phase_determination/visual_phase_timelapse.py
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import torch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "phase_determination"))
import timelapse_phase_eval as tpe  # noqa: E402


def _load_visual_model_module():
    """services/visual_phase/model.py под своим именем: у services/phase тоже
    есть model.py, и ensemble_eval.py импортирует оба."""
    import importlib.util  # noqa: PLC0415

    spec = importlib.util.spec_from_file_location("visual_phase_model", ROOT / "services" / "visual_phase" / "model.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_vpm = _load_visual_model_module()
TRANSFORM, VisualPhaseModel = _vpm.TRANSFORM, _vpm.VisualPhaseModel

OUT = ROOT / "phase_determination" / "timelapse_eval" / "visual"
EMB_CACHE = OUT / "embeddings.npz"
FRAME_FPS = 2.0
FUSION_WEIGHTS = [0.0, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 1.0]
EQUIPMENT_LOVO = {
    "new_yolo": ROOT / "phase_determination" / "timelapse_eval" / "finetune_lovo" / "probs.npz",
    "old_yolo": ROOT / "phase_determination" / "timelapse_eval_old_yolo" / "finetune_lovo" / "probs.npz",
}


def phase_names() -> list[str]:
    return tpe._canonical_phases()


def iter_frames(eval_dir: Path = ROOT / "phase_determination" / "timelapse_eval", videos_dir: Path | None = None):
    """(video, t_s, PIL-кадр) с частотой FRAME_FPS в размеченной части
    каждого ролика (videos.csv/phase_labels.csv в eval_dir) — один и тот же
    набор кадров для любого бэкбона, чтобы их можно было сравнивать."""
    import cv2  # noqa: PLC0415
    from PIL import Image  # noqa: PLC0415

    tpe._set_out_dir(eval_dir)
    videos = tpe._read_videos()
    labels = tpe._read_labels(phase_names())
    for video, meta in videos.items():
        if video not in labels:
            continue
        cap = cv2.VideoCapture(str((videos_dir or tpe.VIDEOS_DIR) / video))
        fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
        step = max(1, round(fps / FRAME_FPS))
        idx = 0
        while cap.grab():
            t = idx / fps
            if idx % step == 0 and meta["start_s"] <= t <= meta["end_s"]:
                _, frame = cap.retrieve()
                yield video, t, Image.fromarray(frame[:, :, ::-1])
            idx += 1
        cap.release()


def extract_embeddings(cache: Path = EMB_CACHE, transform=None, embed=None, **frame_kwargs) -> dict:
    """Эмбеддинги кадров iter_frames(); по умолчанию — бэкбон services/visual_phase.
    transform: PIL -> тензор; embed: батч-тензор -> np.ndarray."""
    if cache.exists():
        z = np.load(cache)
        return {k: z[k] for k in z.files}
    if embed is None:
        device = "cuda" if torch.cuda.is_available() else "cpu"
        model = VisualPhaseModel(ROOT / "services" / "visual_phase" / "weights" / "backbone_best.pt", device=device)
        transform = TRANSFORM

        @torch.no_grad()
        def embed(x):
            return model.backbone(x.to(model.device)).cpu().numpy()

    rec_video, rec_t, rec_emb, batch = [], [], [], []

    def flush():
        rec_emb.append(embed(torch.stack(batch)))
        batch.clear()

    last = None
    for video, t, img in iter_frames(**frame_kwargs):
        if video != last and last is not None:
            print(f"{last[:50]}: {sum(v == last for v in rec_video)} кадров", flush=True)
        last = video
        batch.append(transform(img))
        rec_video.append(video)
        rec_t.append(t)
        if len(batch) == 64:
            flush()
    if batch:
        flush()
    cache.parent.mkdir(parents=True, exist_ok=True)
    data = {"video": np.array(rec_video), "t_s": np.array(rec_t), "emb": np.concatenate(rec_emb)}
    np.savez(cache, **data)
    return data


def fit_head(X, y, n_classes):
    from sklearn.linear_model import LogisticRegression  # noqa: PLC0415
    from sklearn.preprocessing import StandardScaler  # noqa: PLC0415

    scaler = StandardScaler().fit(X)
    clf = LogisticRegression(C=0.05, max_iter=3000, class_weight="balanced").fit(scaler.transform(X), y)
    return scaler, clf


def head_probs(scaler, clf, X, n_classes) -> np.ndarray:
    p = np.full((len(X), n_classes), 1e-4)
    p[:, clf.classes_] = clf.predict_proba(scaler.transform(X))
    return p / p.sum(1, keepdims=True)


def main() -> None:
    names = phase_names()
    pid = {p: i for i, p in enumerate(names)}
    data = extract_embeddings()
    tpe._set_out_dir(ROOT / "phase_determination" / "timelapse_eval")
    marks = tpe._read_labels(names)
    y = np.array([pid.get(tpe._label_at(marks[v], t), -1) for v, t in zip(data["video"], data["t_s"])])
    keep = y >= 0
    V, T, X, y = data["video"][keep], data["t_s"][keep], data["emb"][keep], y[keep]
    videos = sorted(set(V))
    print(f"кадров с эталоном: {len(y)}, роликов: {len(videos)}", flush=True)

    # --- LOVO по кадрам ---
    frame_probs = np.zeros((len(y), len(names)))
    for v in videos:
        tr, te = V != v, V == v
        scaler, clf = fit_head(X[tr], y[tr], len(names))
        frame_probs[te] = head_probs(scaler, clf, X[te], len(names))
    frame_acc = (frame_probs.argmax(1) == y).mean()

    # --- кадры -> дни: последний кадр не позже середины дня ---
    by_video = defaultdict(list)
    for i, (v, t) in enumerate(zip(V, T)):
        by_video[v].append((t, i))
    lines = ["# Визуальный сигнал фазы + объединение с модулем 3 (leave-one-video-out)", "",
             f"Кадров с эталоном: {len(y)} ({FRAME_FPS:g}/сек), роликов: {len(videos)}. "
             f"Визуальная голова по кадрам: точность **{frame_acc:.1%}**.", ""]
    for det, path in EQUIPMENT_LOVO.items():
        if not path.exists():
            lines.append(f"({det}: нет {path.name} — сначала finetune_timelapse.py)")
            continue
        eq = np.load(path)
        eq_names = list(eq["phase_names"])
        assert eq_names == names, "порядок фаз в probs.npz и phase_labels различается"
        vis, eqp, truth, per_video = [], [], [], []
        for v, d, t, p in zip(eq["video"], eq["day"], eq["t_s"], eq["probs"]):
            frames = [i for ft, i in sorted(by_video[v]) if ft <= t]
            if not frames:
                continue
            vis.append(frame_probs[frames[-1]])
            eqp.append(p)
            truth.append(pid[tpe._label_at(marks[v], t)])
            per_video.append(v)
        vis, eqp, truth = np.array(vis), np.array(eqp), np.array(truth)
        per_video = np.array(per_video)

        def scores(w):
            fused = w * np.log(eqp + 1e-6) + (1 - w) * np.log(vis + 1e-6)
            pred = fused.argmax(1)
            acc = (pred == truth).mean()
            bal = np.mean([(pred[truth == c] == c).mean() for c in np.unique(truth)])
            return acc, bal, pred

        lines += [f"## Детекции {det}: {len(truth)} дней", "",
                  "| w (доля техники) | точность | средняя по фазам |", "|---|---|---|"]
        for w in FUSION_WEIGHTS:
            acc, bal, _ = scores(w)
            label = {0.0: " (только визуально)", 1.0: " (только техника)"}.get(w, "")
            lines.append(f"| {w:g}{label} | {acc:.1%} | {bal:.1%} |")
        _, _, pred = scores(0.5)
        lines += ["", "По фазам при w=0.5 (recall):", ""]
        for c in np.unique(truth):
            lines.append(f"- {names[c]}: {(pred[truth == c] == c).mean():.0%} из {(truth == c).sum()} дней")
        lines.append("")

    # --- голова на всех роликах -> формат для services/visual_phase ---
    scaler, clf = fit_head(X, y, len(names))
    head = {
        "type": "linear",
        "backbone": "services/visual_phase/weights/backbone_best.pt",
        "phase_names": names,
        "classes": [names[c] for c in clf.classes_],
        "scaler_mean": scaler.mean_.tolist(),
        "scaler_scale": scaler.scale_.tolist(),
        "coef": clf.coef_.tolist(),
        "intercept": clf.intercept_.tolist(),
        "trained_on": videos,
        "lovo_frame_accuracy": float(frame_acc),
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "linear_head.json").write_text(json.dumps(head), encoding="utf-8")
    (OUT / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
