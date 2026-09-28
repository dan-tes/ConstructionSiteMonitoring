# CLIP / SigLIP для фазы по снимку (кадры таймлапсов, LOVO)

Опора: DINOv2-голова — кадры 50.6%.

## ViT-B-16-SigLIP (webli)
- zero-shot по кадрам: 26.5%
- linear probe LOVO по кадрам: 59.7%

## ViT-L-14 (laion2b_s32b_b82k)
- zero-shot по кадрам: 31.9%
- linear probe LOVO по кадрам: 55.9%

## Ансамбль по дням (8716 дней, детекции прод-YOLO)

| вариант | точность | средняя по фазам |
|---|---|---|
| техника + DINOv2 (сейчас в проде, без 2-й модели техники) | 60.2% | 30.4% |
| ViT-B-16-SigLIP zero-shot отдельно | 31.1% | 26.4% |
| техника + DINOv2 + ViT-B-16-SigLIP zero-shot (w=0.2) | 64.0% | 33.5% |
| техника + DINOv2 + ViT-B-16-SigLIP zero-shot (w=0.3) | 64.6% | 34.4% |
| ViT-B-16-SigLIP probe отдельно | 56.4% | 29.5% |
| техника + DINOv2 + ViT-B-16-SigLIP probe (w=0.2) | 63.8% | 32.1% |
| техника + DINOv2 + ViT-B-16-SigLIP probe (w=0.3) | 64.2% | 32.8% |
| ViT-L-14 zero-shot отдельно | 39.1% | 33.2% |
| техника + DINOv2 + ViT-L-14 zero-shot (w=0.2) | 64.5% | 34.8% |
| техника + DINOv2 + ViT-L-14 zero-shot (w=0.3) | 64.2% | 35.8% |
| ViT-L-14 probe отдельно | 54.7% | 27.4% |
| техника + DINOv2 + ViT-L-14 probe (w=0.2) | 63.7% | 31.7% |
| техника + DINOv2 + ViT-L-14 probe (w=0.3) | 62.5% | 30.9% |
