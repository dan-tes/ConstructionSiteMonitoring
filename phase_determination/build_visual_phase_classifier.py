"""Шаги 4-5 из плана visual_state_pretraining.ipynb ("Дальше"): кластеризация
сохранённых эмбеддингов (`visual_pretraining_embeddings/embeddings.npy`) и
разметка кластеров вручную вместо обучения полноценной головы классификатора
(меток фаз по этим 10к фото нет — см. hf_construction_site_annotations.jsonl,
там только капшны/bbox нарушений, не фаза).

Как заполнялся PHASE_LABELS ниже: для каждого из 8 kmeans-кластеров взяты
5 фото, ближайших к центроиду (см. `_closest_to_centroids()`), и просмотрены
вручную. Дословно, что видно на площадках в каждом кластере:
  0 - котлован с шпунтовым/буронабивным ограждением, свайный молот, бетонная
      подпорная стена по периметру -> Foundation (глубокий нулевой цикл).
  1 - открытая грунтовая площадка, несколько экскаваторов, построек ещё нет
      -> Earthwork (активная разработка грунта).
  2 - опалубка/арматура плиты под краном на дне котлована -> Structural Frame
      (эта же сцена технологически совпадает с 5; оставлены двумя разными
      кластерами, т.к. в эмбеддингах они разошлись по ракурсу/освещению).
  3 - экскаватор грузит грунт/лом в самосвал, дымка -> Earthwork.
  4 - башенные краны, вертикальные бетонные колонны многоэтажного каркаса
      -> Structural Frame.
  5 - рабочие вяжут арматурную сетку плиты в котловане, откосы грунта видны
      -> Foundation (армирование плитного/ростверкового фундамента).
  6 - мощение бордюров/тротуара вдоль забора на благоустроенной территории
      -> External Works.
  7 - склад/цех заготовки арматуры + монтаж стального каркаса краном
      -> Structural Frame.

ВАЖНО (ограничение, а не баг): исходный набор фото (HF `LouisChen15/
ConstructionSite`, см. ноутбук) сам смещён в сторону земляных/фундаментных/
каркасных работ - в нём нет сцен кладки, инженерных сетей (MEP), отделки,
мобилизации площадки или сдачи объекта. При k=8 кластеры естественным
образом покрывают только 4 из 10 канонических фаз (Earthwork, Foundation,
Structural Frame, External Works) - остальные шесть (Preconstruction, Site
Preparation, Masonry, MEP, Finishing, Commissioning) этот классификатор
никогда не предскажет, что бы ни было на фото. `services/visual_phase`
использует это как ДОПОЛНИТЕЛЬНЫЙ сигнал, а не замену equipment-based
модуля (services/phase) - см. backend/integrations/README.md.

Запуск: python build_visual_phase_classifier.py
Вход:  visual_pretraining_embeddings/{embeddings.npy,image_paths.txt}
Выход: data/visual_phase_checkpoints/classifier.json
       (scaler mean/scale, 8 центроидов, cluster_id -> phase mapping)
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from sklearn.cluster import KMeans
from sklearn.preprocessing import StandardScaler

BASE_DIR = Path(__file__).resolve().parent
EMBEDDINGS_DIR = BASE_DIR / "visual_pretraining_embeddings"
OUT_DIR = BASE_DIR / "data" / "visual_phase_checkpoints"

N_CLUSTERS = 8
RANDOM_STATE = 42

# cluster_id -> canonical phase (see module docstring for how each was
# decided). Every canonical phase not listed here has no cluster and will
# never be predicted - that's a known, documented gap, not an oversight.
PHASE_LABELS: dict[int, str] = {
    0: "Foundation",
    1: "Earthwork",
    2: "Structural Frame",
    3: "Earthwork",
    4: "Structural Frame",
    5: "Foundation",
    6: "External Works",
    7: "Structural Frame",
}


def _closest_to_centroids(X: np.ndarray, labels: np.ndarray, centers: np.ndarray, paths: list[str], k: int = 5):
    for c in range(centers.shape[0]):
        idx = np.where(labels == c)[0]
        dists = np.linalg.norm(X[idx] - centers[c], axis=1)
        order = idx[np.argsort(dists)][:k]
        print(f"cluster {c} (n={len(idx)}): " + "; ".join(Path(paths[i]).name for i in order))


def main() -> None:
    embeddings = np.load(EMBEDDINGS_DIR / "embeddings.npy")
    paths = (EMBEDDINGS_DIR / "image_paths.txt").read_text(encoding="utf-8").splitlines()
    if embeddings.shape[0] != len(paths):
        raise ValueError(f"embeddings ({embeddings.shape[0]}) and image_paths ({len(paths)}) disagree")
    print(f"embeddings: {embeddings.shape}")

    scaler = StandardScaler().fit(embeddings)
    X = scaler.transform(embeddings)

    km = KMeans(n_clusters=N_CLUSTERS, random_state=RANDOM_STATE, n_init=10).fit(X)
    print("cluster sizes:", np.bincount(km.labels_).tolist())
    _closest_to_centroids(X, km.labels_, km.cluster_centers_, paths)

    missing = set(range(N_CLUSTERS)) - set(PHASE_LABELS)
    if missing:
        raise ValueError(f"PHASE_LABELS has no entry for cluster(s) {sorted(missing)} - label them by hand first")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "backbone": "vit_small_patch14_dinov2.lvd142m",
        "embedding_dim": int(embeddings.shape[1]),
        "n_clusters": N_CLUSTERS,
        "scaler_mean": scaler.mean_.tolist(),
        "scaler_scale": scaler.scale_.tolist(),
        "centroids": km.cluster_centers_.tolist(),
        "cluster_phase": {str(k): v for k, v in PHASE_LABELS.items()},
        "unsupported_phases": [
            "Preconstruction", "Site Preparation", "Masonry", "MEP", "Finishing", "Commissioning",
        ],
    }
    out_path = OUT_DIR / "classifier.json"
    out_path.write_text(json.dumps(payload, indent=1), encoding="utf-8")
    print(f"saved {out_path}")


if __name__ == "__main__":
    main()
