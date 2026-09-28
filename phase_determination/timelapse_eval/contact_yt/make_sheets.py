"""Контактные листы 4x4 для новых роликов (data/yt/*.mp4) + подсказки о
длительности стройки из названия/описания (info.json) — для ручной разметки."""
import glob, json, os, re, sys
import cv2, numpy as np

OUT = os.path.dirname(os.path.abspath(__file__))
YT = os.path.join(OUT, "..", "..", "..", "data", "yt")
DUR_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*[- ]?\s*(years?|yrs?|months?|weeks?|days?|jahre?n?|monate?n?|wochen|tage?n?|лет|года?|месяц\w*|недел\w*|дн\w*)", re.I)

for path in sorted(glob.glob(os.path.join(YT, "*.mp4"))):
    vid = os.path.basename(path)[:-4]
    sheet = os.path.join(OUT, vid + ".jpg")
    info = json.load(open(os.path.join(YT, vid + ".info.json"))) if os.path.exists(os.path.join(YT, vid + ".info.json")) else {}
    text = (info.get("title", "") + " \n " + (info.get("description") or ""))
    hints = sorted({m.group(0) for m in DUR_RE.finditer(text)})[:6]
    print(f"{vid}\t{info.get('duration')}\t{info.get('title','')[:70]}\t{' | '.join(hints)}")
    if os.path.exists(sheet):
        continue
    c = cv2.VideoCapture(path); n = int(c.get(7)); fps = c.get(5) or 25; tiles = []
    for k in range(16):
        idx = int((k + 0.5) / 16 * n); c.set(cv2.CAP_PROP_POS_FRAMES, idx); ok, f = c.read()
        f = cv2.resize(f, (320, 180)) if ok else np.zeros((180, 320, 3), np.uint8)
        cv2.rectangle(f, (0, 0), (78, 22), (0, 0, 0), -1)
        cv2.putText(f, f"{idx / fps:5.1f}s", (3, 16), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1)
        tiles.append(f)
    cv2.imwrite(sheet, np.vstack([np.hstack(tiles[r * 4:(r + 1) * 4]) for r in range(4)]))
