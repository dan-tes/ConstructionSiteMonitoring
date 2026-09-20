"""Retrain the ConstructionPhaseModel with frequency-weighted (not binary)
per-phase equipment features.

This is a faithful port of construction_phase_training.ipynb with exactly
one change, isolated so its effect can be measured: cells 9 and 15 there
build each phase's "expected equipment" as a *binary* set (any activity of
that phase ever using class X counts the same as every activity using it).
Confirmed by direct testing against the real checkpoint (see
backend/integrations/README.md's Open items): this washes out the signal
needed to tell equipment-overlapping phases apart — tower_crane/hanging_hook
appear in only 6% of MEP's training activities but 52-56% of Structural
Frame's, yet both were fed to the model as an identical "yes, expected".
Real detected counts from a real video (tower_crane, hanging_hook, worker,
other_vehicle, concrete_mixer, excavator, vehicle_crane) were classified as
"MEP" at 90-99% confidence despite concrete_mixer never once appearing in an
MEP activity.

Everything else — data, windowing, noise simulation, model architecture,
training loop, hyperparameters, splits — is unchanged from the notebook, so
any accuracy difference is attributable to this one change.
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import torch
from sentence_transformers import SentenceTransformer
from torch.utils.data import DataLoader, Dataset

sys.path.insert(0, str(Path(__file__).parent.parent / "services" / "phase"))
from model import ConstructionPhaseModel  # noqa: E402

SEED = 42
random.seed(SEED)
np.random.seed(SEED)
torch.manual_seed(SEED)
if torch.cuda.is_available():
    torch.cuda.manual_seed_all(SEED)
device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
print("device:", device, "-", torch.cuda.get_device_name(0) if device.type == "cuda" else "cpu")

PROJECT_DIR = Path(__file__).parent
DATA_DIR = PROJECT_DIR / "data"
ACTIVITIES_PATH = DATA_DIR / "activities_with_equipment.csv"
EVENTS_PATH = DATA_DIR / "equipment_events_with_context.csv"
EQUIPMENT_DESC_PATH = DATA_DIR / "equipment_descriptions.csv"
CHECKPOINT_DIR = DATA_DIR / "construction_phase_checkpoints_weighted"
CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Data (identical to the notebook)
# ---------------------------------------------------------------------------
activities = pd.read_csv(ACTIVITIES_PATH)
events = pd.read_csv(EVENTS_PATH)
equipment_desc = pd.read_csv(EQUIPMENT_DESC_PATH)
activities["expected_equipment"] = activities["expected_equipment"].apply(
    lambda x: eval(x) if isinstance(x, str) else x
)
events["at"] = pd.to_datetime(events["at"])

print("activities:", activities.shape, "events:", events.shape)
print("projects:", activities["project_id"].nunique())

EQUIPMENT_CLASSES = equipment_desc["equipment_class"].tolist()
EQUIPMENT_TO_ID = {x: i for i, x in enumerate(EQUIPMENT_CLASSES)}
NUM_EQUIPMENT = len(EQUIPMENT_CLASSES)
equipment_descriptions = dict(zip(equipment_desc["equipment_class"], equipment_desc["description"]))

# ---------------------------------------------------------------------------
# Phase metadata — THE FIX is here: prevalence fraction, not a binary set.
# ---------------------------------------------------------------------------
phase_meta = (
    activities.groupby(["phase", "phase_order"], as_index=False)
    .agg(
        activities=("activity_name", lambda x: list(dict.fromkeys(x))),
        duration_days=("planned_duration_days", "sum"),
        criticality=("criticality", "mean"),
    )
    .sort_values("phase_order")
    .reset_index(drop=True)
)
PHASE_NAMES = phase_meta["phase"].tolist()
PHASE_TO_ID = {p: i for i, p in enumerate(PHASE_NAMES)}
NUM_PHASES = len(PHASE_NAMES)

phase_equipment_prevalence: dict[str, np.ndarray] = {}
phase_texts = []

for _, row in phase_meta.iterrows():
    phase = row["phase"]
    phase_activities = activities.loc[activities["phase"] == phase, "expected_equipment"]
    n_activities = len(phase_activities)

    counts = np.zeros(NUM_EQUIPMENT, dtype=np.float32)
    for eqs in phase_activities:
        for e in eqs:
            if e in EQUIPMENT_TO_ID:
                counts[EQUIPMENT_TO_ID[e]] += 1
    prevalence = counts / max(n_activities, 1)
    phase_equipment_prevalence[phase] = prevalence

    present = [(EQUIPMENT_CLASSES[i], prevalence[i]) for i in range(NUM_EQUIPMENT) if prevalence[i] > 0]
    present.sort(key=lambda t: -t[1])

    equipment_text = "\n".join(
        f"- {e}: present in {p:.0%} of this phase's activities. {equipment_descriptions[e]}"
        for e, p in present
    )
    activities_text = ", ".join(row["activities"])
    phase_texts.append(
        f"Construction phase: {phase}. Activities: {activities_text}. "
        f"Expected equipment with prevalence: "
        f"{', '.join(f'{e} ({p:.0%})' for e, p in present)}.\n"
        f"Equipment descriptions:\n{equipment_text}"
    )

phase_meta["phase_text"] = phase_texts
print(phase_meta[["phase", "duration_days", "criticality"]].to_string(index=False))
print()
print("Prevalence sanity check (used to be binary 0/1 for all of these):")
for phase in ("MEP", "Structural Frame", "Foundation"):
    prev = phase_equipment_prevalence[phase]
    nonzero = {EQUIPMENT_CLASSES[i]: f"{prev[i]:.0%}" for i in range(NUM_EQUIPMENT) if prev[i] > 0}
    print(f"  {phase}: {nonzero}")

# ---------------------------------------------------------------------------
# Semantic phase embeddings (identical to the notebook, just on the new text)
# ---------------------------------------------------------------------------
TEXT_MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
text_encoder = SentenceTransformer(TEXT_MODEL_NAME, device=str(device))
with torch.no_grad():
    phase_text_embeddings = (
        text_encoder.encode(
            phase_meta["phase_text"].tolist(),
            convert_to_tensor=True,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        .float()
        .cpu()
    )
PHASE_TEXT_DIM = phase_text_embeddings.shape[-1]
print("Phase text embedding shape:", phase_text_embeddings.shape)

# ---------------------------------------------------------------------------
# Windowed project dataset (identical to the notebook)
# ---------------------------------------------------------------------------
OBSERVATION_STEP_HOURS = 24
WINDOW_SIZE = 128
WINDOW_STRIDE = 64

MISS_PROB = 0.08
FALSE_POSITIVE_PROB = 0.01
CONFIDENCE_NOISE = 0.08


def build_project_timeline(project_id: str):
    plan = activities[activities["project_id"] == project_id].sort_values("activity_sequence").copy()
    ev = events[events["project_id"] == project_id].sort_values("at").copy()

    start = ev["at"].min().floor("D")
    end = ev["at"].max().ceil("D")
    timestamps = pd.date_range(start=start + pd.Timedelta(hours=12), end=end, freq=f"{OBSERVATION_STEP_HOURS}h")

    state = {e: 0 for e in EQUIPMENT_CLASSES}
    event_idx = 0
    ev_records = ev[["equipment_class", "event", "at"]].to_dict("records")

    observations = []
    labels = []

    activity_start = start
    intervals = []
    for _, r in plan.iterrows():
        duration = max(float(r["planned_duration_days"]), 1.0)
        activity_end = activity_start + pd.Timedelta(days=duration)
        intervals.append((activity_start, activity_end, PHASE_TO_ID[r["phase"]]))
        activity_start = activity_end

    interval_idx = 0
    for ts in timestamps:
        while event_idx < len(ev_records) and pd.Timestamp(ev_records[event_idx]["at"]) <= ts:
            e = ev_records[event_idx]
            if e["event"] == "arrival":
                state[e["equipment_class"]] += 1
            else:
                state[e["equipment_class"]] = max(0, state[e["equipment_class"]] - 1)
            event_idx += 1

        while interval_idx < len(intervals) - 1 and ts >= intervals[interval_idx][1]:
            interval_idx += 1

        if not intervals:
            continue

        start_i, end_i, phase_id = intervals[interval_idx]
        if start_i <= ts < end_i:
            labels.append(phase_id)
        else:
            labels.append(intervals[max(0, min(interval_idx, len(intervals) - 1))][2])

        counts = np.array([state[e] for e in EQUIPMENT_CLASSES], dtype=np.float32)
        visible = np.ones(NUM_EQUIPMENT, dtype=np.float32)
        confidence = np.ones(NUM_EQUIPMENT, dtype=np.float32)
        for i in range(NUM_EQUIPMENT):
            if counts[i] > 0 and random.random() < MISS_PROB:
                visible[i] = 0.0
                confidence[i] = 0.0
            elif counts[i] > 0:
                confidence[i] = np.clip(np.random.normal(0.9, CONFIDENCE_NOISE), 0.35, 1.0)
            if counts[i] == 0 and random.random() < FALSE_POSITIVE_PROB:
                visible[i] = 1.0
                confidence[i] = np.clip(np.random.normal(0.55, CONFIDENCE_NOISE), 0.2, 0.9)

        count_feature = np.clip(counts, 0, 5) / 5.0
        obs = np.concatenate([count_feature, visible, confidence]).astype(np.float32)
        observations.append(obs)

    return (
        np.asarray(observations, dtype=np.float32),
        np.asarray(labels, dtype=np.int64),
        timestamps[: len(labels)],
    )


class ConstructionWindowDataset(Dataset):
    def __init__(self, project_ids):
        self.samples = []
        for project_id in project_ids:
            observations, labels, timestamps = build_project_timeline(project_id)
            if len(labels) == 0:
                continue
            if len(labels) <= WINDOW_SIZE:
                self.samples.append((observations, labels, timestamps, project_id))
                continue
            for start in range(0, len(labels) - WINDOW_SIZE + 1, WINDOW_STRIDE):
                end = start + WINDOW_SIZE
                self.samples.append(
                    (observations[start:end], labels[start:end], timestamps[start:end], project_id)
                )
            if (len(labels) - WINDOW_SIZE) % WINDOW_STRIDE != 0:
                start = len(labels) - WINDOW_SIZE
                self.samples.append(
                    (observations[start:], labels[start:], timestamps[start:], project_id)
                )

    def __len__(self):
        return len(self.samples)

    def __getitem__(self, idx):
        observations, labels, timestamps, project_id = self.samples[idx]
        return {
            "observations": torch.from_numpy(observations),
            "labels": torch.from_numpy(labels),
            "timestamps": list(timestamps),
            "project_id": project_id,
        }


all_projects = activities[["project_id", "split"]].drop_duplicates()
train_projects = all_projects.loc[all_projects["split"] == "train", "project_id"].tolist()
val_projects = all_projects.loc[all_projects["split"] == "validation", "project_id"].tolist()
test_projects = all_projects.loc[all_projects["split"] == "test", "project_id"].tolist()

train_dataset = ConstructionWindowDataset(train_projects)
val_dataset = ConstructionWindowDataset(val_projects)
test_dataset = ConstructionWindowDataset(test_projects)
print("Train projects:", len(train_projects), "windows:", len(train_dataset))
print("Val projects:", len(val_projects), "windows:", len(val_dataset))
print("Test projects:", len(test_projects), "windows:", len(test_dataset))

# ---------------------------------------------------------------------------
# collate_batch — THE FIX applied here too: prevalence fraction, not 1.0.
# ---------------------------------------------------------------------------
duration_arr = phase_meta["duration_days"].to_numpy(dtype=np.float32)
duration_arr = duration_arr / max(duration_arr.max(), 1.0)
criticality_arr = phase_meta["criticality"].to_numpy(dtype=np.float32)

_structured_rows = []
for phase in PHASE_NAMES:
    pidx = PHASE_TO_ID[phase]
    _structured_rows.append(
        np.concatenate(
            [
                phase_equipment_prevalence[phase],
                np.array([duration_arr[pidx], criticality_arr[pidx]], dtype=np.float32),
            ]
        )
    )
PHASE_STRUCTURED_BASE = torch.tensor(np.stack(_structured_rows), dtype=torch.float32)  # (NUM_PHASES, NUM_EQUIPMENT + 2)


def collate_batch(batch):
    observations = torch.stack([x["observations"] for x in batch])
    labels = torch.stack([x["labels"] for x in batch])
    B = observations.shape[0]
    phase_text = phase_text_embeddings.unsqueeze(0).expand(B, -1, -1).clone()
    phase_structured = PHASE_STRUCTURED_BASE.unsqueeze(0).expand(B, -1, -1).clone()
    return {
        "observations": observations,
        "labels": labels,
        "phase_text_embeddings": phase_text,
        "phase_structured": phase_structured,
        "timestamps": [x["timestamps"] for x in batch],
        "project_id": [x["project_id"] for x in batch],
    }


BATCH_SIZE = 8
NUM_WORKERS = 2
train_loader = DataLoader(
    train_dataset, batch_size=BATCH_SIZE, shuffle=True, num_workers=NUM_WORKERS,
    pin_memory=(device.type == "cuda"), collate_fn=collate_batch,
)
val_loader = DataLoader(
    val_dataset, batch_size=BATCH_SIZE, shuffle=False, num_workers=NUM_WORKERS,
    pin_memory=(device.type == "cuda"), collate_fn=collate_batch,
)
test_loader = DataLoader(
    test_dataset, batch_size=BATCH_SIZE, shuffle=False, num_workers=NUM_WORKERS,
    pin_memory=(device.type == "cuda"), collate_fn=collate_batch,
)

batch = next(iter(train_loader))
print("Observation batch:", batch["observations"].shape)
print("Phase structured batch:", batch["phase_structured"].shape)

# ---------------------------------------------------------------------------
# Model / training (identical to the notebook)
# ---------------------------------------------------------------------------
OBSERVATION_DIM = NUM_EQUIPMENT * 3
PHASE_STRUCTURED_DIM = NUM_EQUIPMENT + 2


def phase_classification_loss(emissions, labels):
    B, T, P = emissions.shape
    return torch.nn.functional.cross_entropy(emissions.reshape(B * T, P), labels.reshape(B * T))


def save_checkpoint(model, optimizer, scheduler, epoch, train_metrics, val_metrics, best_val_loss, path):
    torch.save(
        {
            "epoch": epoch,
            "model_state_dict": model.state_dict(),
            "optimizer_state_dict": optimizer.state_dict(),
            "scheduler_state_dict": scheduler.state_dict() if scheduler else None,
            "train_metrics": train_metrics,
            "val_metrics": val_metrics,
            "best_val_loss": best_val_loss,
            "phase_names": PHASE_NAMES,
            "equipment_classes": EQUIPMENT_CLASSES,
            "config": {
                "window_size": WINDOW_SIZE,
                "observation_dim": OBSERVATION_DIM,
                "phase_text_dim": PHASE_TEXT_DIM,
                "phase_structured_dim": PHASE_STRUCTURED_DIM,
            },
        },
        path,
    )


def run_epoch(model, loader, optimizer=None, scaler=None):
    is_train = optimizer is not None
    model.train(is_train)
    total_loss = 0.0
    total_correct = 0
    total_count = 0
    for batch in loader:
        observations = batch["observations"].to(device, non_blocking=True)
        phase_text = batch["phase_text_embeddings"].to(device, non_blocking=True)
        phase_structured = batch["phase_structured"].to(device, non_blocking=True)
        labels = batch["labels"].to(device, non_blocking=True)
        if is_train:
            optimizer.zero_grad(set_to_none=True)
        with torch.set_grad_enabled(is_train):
            output = model(observations=observations, phase_text_embeddings=phase_text, phase_structured=phase_structured)
            loss = phase_classification_loss(output.emissions, labels)
            if is_train:
                if scaler is not None:
                    scaler.scale(loss).backward()
                    scaler.unscale_(optimizer)
                    torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
                    scaler.step(optimizer)
                    scaler.update()
                else:
                    loss.backward()
                    torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
                    optimizer.step()
        pred = output.emissions.argmax(dim=-1)
        total_loss += loss.item() * observations.size(0)
        total_correct += (pred == labels).sum().item()
        total_count += labels.numel()
    return {"loss": total_loss / max(len(loader.dataset), 1), "accuracy": total_correct / max(total_count, 1)}


D_MODEL = 256
NHEAD = 8
NUM_LAYERS = 4
DIM_FEEDFORWARD = 1024
DROPOUT = 0.1
EPOCHS = 30
LEARNING_RATE = 3e-4
WEIGHT_DECAY = 1e-4

model = ConstructionPhaseModel(
    observation_dim=OBSERVATION_DIM,
    phase_text_dim=PHASE_TEXT_DIM,
    phase_structured_dim=PHASE_STRUCTURED_DIM,
    d_model=D_MODEL,
    nhead=NHEAD,
    num_layers=NUM_LAYERS,
    dim_feedforward=DIM_FEEDFORWARD,
    dropout=DROPOUT,
    max_len=WINDOW_SIZE,
).to(device)
optimizer = torch.optim.AdamW(model.parameters(), lr=LEARNING_RATE, weight_decay=WEIGHT_DECAY)
scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=EPOCHS)
scaler = torch.amp.GradScaler("cuda", enabled=(device.type == "cuda"))
print("Parameters:", sum(p.numel() for p in model.parameters()))

best_val_loss = float("inf")
for epoch in range(1, EPOCHS + 1):
    train_metrics = run_epoch(model, train_loader, optimizer=optimizer, scaler=scaler)
    val_metrics = run_epoch(model, val_loader, optimizer=None, scaler=None)
    scheduler.step()

    epoch_path = CHECKPOINT_DIR / f"checkpoint_epoch_{epoch:03d}.pt"
    save_checkpoint(model, optimizer, scheduler, epoch, train_metrics, val_metrics, best_val_loss, epoch_path)

    if val_metrics["loss"] < best_val_loss:
        best_val_loss = val_metrics["loss"]
        save_checkpoint(model, optimizer, scheduler, epoch, train_metrics, val_metrics, best_val_loss, CHECKPOINT_DIR / "best.pt")
        best_marker = "  <-- BEST"
    else:
        best_marker = ""

    lr = optimizer.param_groups[0]["lr"]
    print(
        f"Epoch {epoch:03d}/{EPOCHS} | lr={lr:.2e} | train loss={train_metrics['loss']:.4f} | "
        f"train acc={train_metrics['accuracy']:.4f} | val loss={val_metrics['loss']:.4f} | "
        f"val acc={val_metrics['accuracy']:.4f}{best_marker}"
    )

print("Training finished. Best validation loss:", best_val_loss)

# ---------------------------------------------------------------------------
# Test evaluation
# ---------------------------------------------------------------------------
checkpoint = torch.load(CHECKPOINT_DIR / "best.pt", map_location=device)
model.load_state_dict(checkpoint["model_state_dict"])
model.eval()
test_metrics = run_epoch(model, test_loader, optimizer=None, scaler=None)
print("Test loss:", test_metrics["loss"])
print("Test accuracy:", test_metrics["accuracy"])

from sklearn.metrics import classification_report

all_true, all_pred = [], []
with torch.no_grad():
    for batch in test_loader:
        observations = batch["observations"].to(device)
        phase_text = batch["phase_text_embeddings"].to(device)
        phase_structured = batch["phase_structured"].to(device)
        labels = batch["labels"].to(device)
        output = model(observations=observations, phase_text_embeddings=phase_text, phase_structured=phase_structured)
        pred = output.emissions.argmax(dim=-1)
        all_true.append(labels.cpu().numpy().reshape(-1))
        all_pred.append(pred.cpu().numpy().reshape(-1))
all_true = np.concatenate(all_true)
all_pred = np.concatenate(all_pred)
print(classification_report(all_true, all_pred, labels=list(range(NUM_PHASES)), target_names=PHASE_NAMES, zero_division=0))
