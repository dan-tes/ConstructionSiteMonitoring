# ML-модели и исследования

В продакшене модели работают внутри `services/*`. Папки ниже содержат, откуда
эти модели взялись: обучение, эксперименты и оценку. У каждого скрипта в
docstring написано, что он делает и зачем. Описание модулей и их качества
см. в [modules/](modules/), сводка — в [results.md](results.md).

Данные и крупные артефакты в git не хранятся: `data/`,
`phase_determination/data/`, `*.mp4`, `*.pt`, `*.npy`. Список исключений
в `.gitignore`.

## Детекция техники: `cv/` → `services/vision`

- `b.ipynb`: обучение YOLO на датасете MOCS (Kaggle).
- `finetune_mocs_colab.ipynb`: дообучение на MOCS и дополнительных
  датасетах техники в Colab. 13 канонических классов.
- `a.ipynb`: прогон детектора по видео.

Веса для продакшена лежат в `services/vision/weights/best.pt`.

## Фаза по технике: `phase_determination/` → `services/phase`

- `construction_phase_training.ipynb`: обучение первой версии
  ConstructionPhaseModel на синтетических рядах вида «работы → ожидаемая
  техника → события».
- `train_phase_v2.py`: вторая версия, обученная на выходе симулятора
  детектора, то есть на том, что модель реально получает в продакшене.
- `finetune_timelapse.py`: дообучение на реальных таймлапсах с оценкой
  leave-one-video-out.
- `timelapse_phase_eval.py`, `compare_detectors.py`: проверка на
  таймлапсах, сравнение весов YOLO.
- `retrain_weighted_equipment.py`: эксперимент, где ожидаемая техника
  задаётся частотными признаками, а не бинарными.

## Фаза по снимку: `phase_determination/` → `services/visual_phase`

- `visual_state_pretraining.ipynb`: self-supervised дообучение DINOv2
  примерно на 10 тыс. неразмеченных фото строек.
- `build_visual_phase_classifier.py`: первый классификатор, k-means по
  эмбеддингам с ручной разметкой кластеров.
- `visual_phase_timelapse.py`: линейная голова, обученная на кадрах
  размеченных таймлапсов.
- `clip_visual_eval.py`: сравнение с CLIP и SigLIP, включая zero-shot.
- `build_visual_ensemble.py`: итоговый ансамбль из DINOv2, SigLIP и
  SigLIP zero-shot, обученный на 60 размеченных стройках.

## Ансамбль и оценка

- `eval_all.py`, `ensemble_eval.py`, `eval_prod_on_new.py`: held-out
  оценка объединённого сигнала «техника + снимок».
- Результаты сохранены в `timelapse_eval*/**/summary.md`, разметка фаз в
  `phase_labels.csv`.

Текущий результат: 66.4% правильно определённых дней на стройках, которые
модель не видела при обучении (10 фолдов по стройкам). Объединение делается
в `services/phase/fusion.py`.

## Прогноз отставания: `latency_prediction/` → `services/delay`

`delay_forecasting_baseline.ipynb` содержит baseline по методу Earned
Schedule. Функция `forecast_delay()` из него без изменений перенесена в
`services/delay/worker.py`.
