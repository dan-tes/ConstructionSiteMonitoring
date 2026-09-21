"""VisualPhaseModel — DINOv2 backbone fine-tuned self-supervised (SimSiam) on
unlabeled site photos, copied from phase_determination/visual_pretraining_checkpoints/best.pt
(see phase_determination/visual_state_pretraining.ipynb for how it was trained).

Only the backbone half of that checkpoint is used here: the SimSiam
projector/predictor heads existed to produce the self-supervised training
signal and have no role at inference time — `_strip_backbone_prefix()` pulls
just the `backbone.*` weights out of the full SimSiamModel state dict.

Preprocessing (`TRANSFORM`) matches the notebook's `EVAL_TRANSFORM` exactly
(same resize/crop/normalize) - the backbone was fine-tuned expecting that,
not the two-view training augmentation.
"""
from __future__ import annotations

from pathlib import Path

import timm
import torch
from PIL import Image
from torchvision import transforms

BACKBONE_NAME = "vit_small_patch14_dinov2.lvd142m"
IMAGE_SIZE = 224
IMAGENET_MEAN = (0.485, 0.456, 0.406)
IMAGENET_STD = (0.229, 0.224, 0.225)

TRANSFORM = transforms.Compose(
    [
        transforms.Resize(256, interpolation=transforms.InterpolationMode.BICUBIC),
        transforms.CenterCrop(IMAGE_SIZE),
        transforms.ToTensor(),
        transforms.Normalize(IMAGENET_MEAN, IMAGENET_STD),
    ]
)


def _strip_backbone_prefix(state_dict: dict) -> dict:
    prefix = "backbone."
    stripped = {k[len(prefix):]: v for k, v in state_dict.items() if k.startswith(prefix)}
    if not stripped:
        raise ValueError("checkpoint has no 'backbone.*' keys - wrong file, or SimSiamModel changed")
    return stripped


class VisualPhaseModel:
    def __init__(self, checkpoint_path: Path, device: str = "cpu"):
        self.device = torch.device(device)
        self.backbone = timm.create_model(
            BACKBONE_NAME,
            pretrained=False,
            num_classes=0,
            img_size=IMAGE_SIZE,
            dynamic_img_size=True,
        )
        checkpoint = torch.load(checkpoint_path, map_location="cpu")
        self.backbone.load_state_dict(_strip_backbone_prefix(checkpoint["model_state_dict"]))
        self.backbone.to(self.device).eval()

    @torch.no_grad()
    def embed(self, image: Image.Image) -> torch.Tensor:
        """RGB PIL image -> (embedding_dim,) backbone feature vector, same
        space as phase_determination/visual_pretraining_embeddings/embeddings.npy
        (that file was produced by this same backbone/transform, see the
        notebook's step 10)."""
        x = TRANSFORM(image.convert("RGB")).unsqueeze(0).to(self.device)
        return self.backbone(x)[0].cpu()
