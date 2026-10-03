"""Shared helpers for model training: artifact IO, metrics file, evaluation metrics."""

from __future__ import annotations

import json
from pathlib import Path

import joblib
import numpy as np
from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score

from app.config import BACKEND_DIR

ARTIFACTS_DIR = BACKEND_DIR / "artifacts"
METRICS_FILE = "metrics.json"

# Extra Profile A worlds (different seeds) used to give the models enough rows.
# Each world is split by time; its last months are never used for training.
TRAIN_SEEDS = (42, 1042, 2042, 3042, 4042)


def save_artifact(name: str, obj: object, directory: Path = ARTIFACTS_DIR) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}.joblib"
    joblib.dump(obj, path)
    return path


def load_artifact(name: str, directory: Path = ARTIFACTS_DIR) -> object:
    return joblib.load(directory / f"{name}.joblib")


def update_metrics(key: str, value: dict, directory: Path = ARTIFACTS_DIR) -> Path:
    """Write one model's metrics into metrics.json, keeping the other models' entries."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / METRICS_FILE
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    data[key] = value
    path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    return path


def calibration_bins(y: np.ndarray, p: np.ndarray, bins: int = 5) -> list[dict]:
    """Equal-count bins of predicted probability: mean prediction vs observed rate."""
    order = np.argsort(p)
    out = []
    for chunk in np.array_split(order, bins):
        if len(chunk):
            out.append(
                {
                    "mean_predicted": round(float(p[chunk].mean()), 4),
                    "observed_rate": round(float(y[chunk].mean()), 4),
                    "n": int(len(chunk)),
                }
            )
    return out


def binary_metrics(y, p) -> dict:
    y, p = np.asarray(y, dtype=int), np.asarray(p, dtype=float)
    both_classes = 0 < y.sum() < len(y)
    return {
        "n": int(len(y)),
        "positive_rate": round(float(y.mean()), 4),
        "pr_auc": round(float(average_precision_score(y, p)), 4) if both_classes else None,
        "roc_auc": round(float(roc_auc_score(y, p)), 4) if both_classes else None,
        "brier": round(float(brier_score_loss(y, p)), 4),
        "calibration": calibration_bins(y, p),
    }
