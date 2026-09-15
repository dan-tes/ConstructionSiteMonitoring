"""ConstructionPhaseModel architecture — copied from
phase_determination/construction_phase_training.ipynb (cell 17, "## 8.
Model") so `best.pt`'s state dict has somewhere to load into outside the
notebook. Keep this in sync by hand if the notebook's architecture changes;
the state dict in weights/best.pt is what pins the two together, so a
mismatch fails loudly (`load_state_dict`) rather than silently.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import torch
import torch.nn as nn


@dataclass
class PhaseModelOutput:
    emissions: torch.Tensor
    phase_embeddings: torch.Tensor
    temporal_embeddings: torch.Tensor


class ObservationEncoder(nn.Module):
    def __init__(self, observation_dim, d_model=256, dropout=0.1):
        super().__init__()
        self.network = nn.Sequential(
            nn.Linear(observation_dim, d_model),
            nn.GELU(),
            nn.LayerNorm(d_model),
            nn.Dropout(dropout),
            nn.Linear(d_model, d_model),
            nn.GELU(),
            nn.LayerNorm(d_model),
        )

    def forward(self, x):
        return self.network(x)


class SemanticPhaseEncoder(nn.Module):
    def __init__(self, text_embedding_dim, structured_dim, d_model=256, dropout=0.1):
        super().__init__()
        self.text_projection = nn.Linear(text_embedding_dim, d_model)
        self.structured_projection = nn.Linear(structured_dim, d_model)
        self.fusion = nn.Sequential(
            nn.Linear(2 * d_model, d_model),
            nn.GELU(),
            nn.LayerNorm(d_model),
            nn.Dropout(dropout),
        )

    def forward(self, text_embeddings, structured_features):
        text = self.text_projection(text_embeddings)
        structured = self.structured_projection(structured_features)
        return self.fusion(torch.cat([text, structured], dim=-1))


class SinusoidalPositionalEncoding(nn.Module):
    def __init__(self, d_model, max_len=4096):
        super().__init__()
        position = torch.arange(max_len, dtype=torch.float32).unsqueeze(1)
        div_term = torch.exp(
            torch.arange(0, d_model, 2, dtype=torch.float32) * (-math.log(10000.0) / d_model)
        )
        pe = torch.zeros(max_len, d_model)
        pe[:, 0::2] = torch.sin(position * div_term)
        pe[:, 1::2] = torch.cos(position * div_term)
        self.register_buffer("pe", pe.unsqueeze(0), persistent=False)

    def forward(self, x):
        if x.size(1) > self.pe.size(1):
            raise ValueError(
                f"Sequence length {x.size(1)} exceeds max positional length {self.pe.size(1)}."
            )
        return x + self.pe[:, : x.size(1)].to(dtype=x.dtype)


class TemporalTransformer(nn.Module):
    def __init__(self, d_model=256, nhead=8, num_layers=4, dim_feedforward=1024, dropout=0.1):
        super().__init__()
        layer = nn.TransformerEncoderLayer(
            d_model=d_model,
            nhead=nhead,
            dim_feedforward=dim_feedforward,
            dropout=dropout,
            activation="gelu",
            batch_first=True,
            norm_first=True,
        )
        self.encoder = nn.TransformerEncoder(layer, num_layers=num_layers)
        self.norm = nn.LayerNorm(d_model)

    def forward(self, x, padding_mask=None):
        return self.norm(self.encoder(x, src_key_padding_mask=padding_mask))


class PhaseScorer(nn.Module):
    def __init__(self, d_model=256):
        super().__init__()
        self.observation_projection = nn.Linear(d_model, d_model, bias=False)
        self.phase_projection = nn.Linear(d_model, d_model, bias=False)
        self.scale = d_model**-0.5

    def forward(self, temporal, phases):
        q = self.observation_projection(temporal)
        k = self.phase_projection(phases)
        return torch.einsum("btd,bpd->btp", q, k) * self.scale


class ConstructionPhaseModel(nn.Module):
    def __init__(
        self,
        observation_dim,
        phase_text_dim,
        phase_structured_dim,
        d_model=256,
        nhead=8,
        num_layers=4,
        dim_feedforward=1024,
        dropout=0.1,
        max_len=4096,
    ):
        super().__init__()
        self.observation_encoder = ObservationEncoder(observation_dim, d_model, dropout)
        self.phase_encoder = SemanticPhaseEncoder(
            phase_text_dim, phase_structured_dim, d_model, dropout
        )
        self.position_encoding = SinusoidalPositionalEncoding(d_model, max_len)
        self.temporal_transformer = TemporalTransformer(
            d_model, nhead, num_layers, dim_feedforward, dropout
        )
        self.phase_scorer = PhaseScorer(d_model)

    def forward(self, observations, phase_text_embeddings, phase_structured, padding_mask=None):
        x = self.observation_encoder(observations)
        x = self.position_encoding(x)
        temporal = self.temporal_transformer(x, padding_mask)
        phases = self.phase_encoder(phase_text_embeddings, phase_structured)
        emissions = self.phase_scorer(temporal, phases)
        return PhaseModelOutput(
            emissions=emissions, phase_embeddings=phases, temporal_embeddings=temporal
        )
