"""Честная проверка текущего прод-ансамбля на 46 новых стройках (data/yt),
которых он не видел: модуль 3 = v2_ft (дообучен на 14 старых роликах) x2 +
v2 x1, визуальная голова = DINOv2 + логрегрессия на кадрах 14 старых роликов
(services/visual_phase/weights/classifier.json), объединение 0.6/0.4 как в
backend/phase_ensemble.py."""
import json, sys
from pathlib import Path
import numpy as np
ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "services" / "phase"), str(ROOT / "phase_determination")]
import timelapse_phase_eval as tpe, train_phase_v2 as t2, ensemble_eval as ee, eval_all as ea, clip_visual_eval as cve
from features import build_phase_meta

names, classes, _, _ = build_phase_meta(t2.DATA_DIR); pid = {p: i for i, p in enumerate(names)}
PD = ROOT / "phase_determination"
for det, folder in (("new_yolo", "timelapse_eval_yt"), ("old_yolo", "timelapse_eval_yt_old_yolo")):
    tpe._set_out_dir(PD / folder); series = tpe.load_series(names)
    vd = [(v, d) for v, s in series.items() for d, l in enumerate(s["labels"]) if l]
    videos = np.array([v for v, _ in vd]); t_s = np.array([series[v]["t_mid"][d] for v, d in vd])
    truth = np.array([pid[series[v]["labels"][d]] for v, d in vd])
    ft14 = ee.v2_base_probs(series, vd, names, classes, t2.DATA_DIR / "construction_phase_checkpoints_v2_ft" / "best.pt")
    v2 = ee.v2_base_probs(series, vd, names, classes)
    eq = ea.loglin([2 / 3, 1 / 3], [ft14, v2])
    head = json.loads((ROOT / "services/visual_phase/weights/classifier.json").read_text())
    e = ea.embeddings("dino"); m = np.isin(e["video"], list(series))
    X = (e["emb"][m] - np.array(head["scaler_mean"])) / np.array(head["scaler_scale"])
    logit = X @ np.array(head["coef"]).T + np.array(head["intercept"])
    fp = np.full((len(X), len(names)), 1e-4); cls = [names.index(c) for c in head["classes"]]
    ex = np.exp(logit - logit.max(1, keepdims=True)); fp[:, cls] = ex / ex.sum(1, keepdims=True)
    vis = cve.to_days(fp, e["video"][m], e["t_s"][m], videos, t_s)
    ok = ~np.isnan(vis).any(1)
    def acc(p):
        pred = p[ok].argmax(1); tr = truth[ok]
        return f"{(pred == tr).mean():.1%} (средняя по фазам {np.mean([(pred[tr == c] == c).mean() for c in np.unique(tr)]):.1%})"
    print(f"== {det}: {ok.sum()} дней, {len(series)} строек")
    print("  модуль 3 (прод, 2 модели):", acc(eq))
    print("  визуальная голова (прод):   ", acc(vis))
    print("  итоговый ансамбль 0.6/0.4:  ", acc(ea.loglin([0.6, 0.4], [eq, vis])))
