"""Train the per-account LSTM sequence model on real transaction histories.

Sequences: last 64 transactions per account (accounts with 5+ transactions),
labelled with ``is_fraud`` of the account's most recent transaction. Features
are standardized; the scaler parameters are stored in the checkpoint so
inference can replicate them.
"""

from __future__ import annotations

import argparse
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import torch
import torch.nn as nn
from sklearn.metrics import recall_score, roc_auc_score
from sklearn.model_selection import train_test_split
from torch.utils.data import DataLoader, TensorDataset

from ml.training.common import (
    MODELS_DIR,
    log_mlflow_run,
    measure_latency_ms,
    print_box,
    save_metrics,
)
from ml.training.prepare_features import DEFAULT_LABELED_TABLE, load_labeled_table

EXPERIMENT_NAME = "behavior_agent_lstm"
MODEL_FILENAME = "lstm_model.pt"

SEQUENCE_WINDOW = 64
MIN_HISTORY = 5  # accounts need 5+ transactions for a meaningful sequence

SEQUENCE_FEATURES: tuple[str, ...] = (
    "amount_npr",
    "log_amount_npr",
    "hour_of_day",
    "is_night",
    "vel_z_score_amount",
    "vel_txn_count_1h",
    "geo_prev_txn_km",
    "geo_prev_txn_time_delta_min",
    "amount_ratio",
)

EPOCHS = 10
BATCH_SIZE = 256
LEARNING_RATE = 1e-3
RANDOM_STATE = 42


class FraudLSTM(nn.Module):
    """Stacked LSTM + linear head (logits out; sigmoid applied at inference).

    Layer names (``lstm`` / ``head``) match the Behavior Agent's loader.
    """

    def __init__(
        self,
        input_dim: int,
        hidden_dim: int = 64,
        num_layers: int = 2,
        dropout: float = 0.3,
    ) -> None:
        super().__init__()
        self.lstm = nn.LSTM(
            input_dim,
            hidden_dim,
            num_layers=num_layers,
            batch_first=True,
            dropout=dropout if num_layers > 1 else 0.0,
        )
        self.head = nn.Sequential(
            nn.Linear(hidden_dim, 32),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(32, 1),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        out, _ = self.lstm(x)
        return self.head(out[:, -1, :])


@dataclass
class SequenceData:
    sequences: np.ndarray  # (n, window, features)
    labels: np.ndarray  # (n,)
    n_accounts_total: int
    feature_means: np.ndarray
    feature_stds: np.ndarray


def build_sequences(df: pd.DataFrame) -> SequenceData:
    work = df.copy()
    work["timestamp"] = pd.to_datetime(work["timestamp"], errors="coerce")
    work = work.sort_values(["account_id", "timestamp"])

    for col in SEQUENCE_FEATURES:
        work[col] = pd.to_numeric(work[col], errors="coerce").fillna(0.0)
    work["_label"] = pd.to_numeric(work["is_fraud"], errors="coerce").fillna(0).astype(int)

    feats = work[list(SEQUENCE_FEATURES)].to_numpy(dtype=np.float32)
    means = feats.mean(axis=0)
    stds = feats.std(axis=0)
    stds[stds == 0] = 1.0
    feats = (feats - means) / stds

    sequences: list[np.ndarray] = []
    labels: list[int] = []
    n_accounts_total = work["account_id"].nunique()

    group_indices = work.groupby("account_id", sort=False).indices
    for _, idx in group_indices.items():
        if len(idx) < MIN_HISTORY:
            continue
        rows = feats[idx][-SEQUENCE_WINDOW:]
        if len(rows) < SEQUENCE_WINDOW:
            pad = np.zeros((SEQUENCE_WINDOW - len(rows), len(SEQUENCE_FEATURES)), dtype=np.float32)
            rows = np.vstack([pad, rows])
        sequences.append(rows)
        labels.append(int(work["_label"].iloc[idx[-1]]))

    return SequenceData(
        sequences=np.asarray(sequences, dtype=np.float32),
        labels=np.asarray(labels, dtype=np.float32),
        n_accounts_total=n_accounts_total,
        feature_means=means,
        feature_stds=stds,
    )


def train_lstm(feature_table_path: Path | None = None, *, epochs: int = EPOCHS) -> dict:
    started = time.perf_counter()
    torch.manual_seed(RANDOM_STATE)
    np.random.seed(RANDOM_STATE)

    df = load_labeled_table(feature_table_path)
    data = build_sequences(df)
    n_sequences = len(data.labels)
    n_fraud_sequences = int(data.labels.sum())
    print(
        f"Built {n_sequences:,} sequences from {data.n_accounts_total:,} accounts "
        f"({n_fraud_sequences:,} fraud-labelled)"
    )

    stratify = data.labels if 0 < n_fraud_sequences < n_sequences else None
    X_train, X_val, y_train, y_val = train_test_split(
        data.sequences,
        data.labels,
        test_size=0.2,
        stratify=stratify,
        random_state=RANDOM_STATE,
    )

    X_train_t = torch.tensor(X_train)
    y_train_t = torch.tensor(y_train).unsqueeze(1)
    X_val_t = torch.tensor(X_val)
    y_val_np = y_val.astype(int)

    pos = float(y_train.sum())
    pos_weight = torch.tensor([(len(y_train) - pos) / max(pos, 1.0)])
    loader = DataLoader(
        TensorDataset(X_train_t, y_train_t), batch_size=BATCH_SIZE, shuffle=True
    )

    model = FraudLSTM(input_dim=len(SEQUENCE_FEATURES))
    optimiser = torch.optim.Adam(model.parameters(), lr=LEARNING_RATE)
    loss_fn = nn.BCEWithLogitsLoss(pos_weight=pos_weight)

    final_loss = 0.0
    val_auroc = 0.0
    val_recall = 0.0
    epoch_logs: list[dict[str, float]] = []

    for epoch in range(epochs):
        model.train()
        epoch_loss = 0.0
        for x_batch, y_batch in loader:
            optimiser.zero_grad()
            loss = loss_fn(model(x_batch), y_batch)
            loss.backward()
            optimiser.step()
            epoch_loss += float(loss.item()) * len(x_batch)
        final_loss = epoch_loss / len(X_train_t)

        model.eval()
        with torch.no_grad():
            val_proba = torch.sigmoid(model(X_val_t)).reshape(-1).numpy()
        val_pred = (val_proba >= 0.5).astype(int)
        val_auroc = (
            float(roc_auc_score(y_val_np, val_proba)) if len(np.unique(y_val_np)) > 1 else 0.0
        )
        val_recall = float(recall_score(y_val_np, val_pred, zero_division=0))
        print(
            f"Epoch {epoch + 1:>2}/{epochs}  loss={final_loss:.4f}  "
            f"val_auroc={val_auroc:.3f}  val_recall={val_recall:.3f}"
        )
        epoch_logs.append({"loss": final_loss, "val_auroc": val_auroc, "val_recall": val_recall})

    # Final validation metrics.
    model.eval()
    with torch.no_grad():
        val_proba = torch.sigmoid(model(X_val_t)).reshape(-1).numpy()
    val_pred = (val_proba >= 0.5).astype(int)
    from sklearn.metrics import confusion_matrix, f1_score

    val_f1 = float(f1_score(y_val_np, val_pred, zero_division=0))
    tn, fp, fn, tp = confusion_matrix(y_val_np, val_pred, labels=[0, 1]).ravel()
    val_fpr = float(fp / (fp + tn)) if (fp + tn) else 0.0

    single = torch.tensor(X_val[:1])
    latency_single = measure_latency_ms(lambda: model(single))

    model_path = MODELS_DIR / MODEL_FILENAME
    torch.save(
        {
            "state_dict": model.state_dict(),
            "input_dim": len(SEQUENCE_FEATURES),
            "sequence_window": SEQUENCE_WINDOW,
            "feature_columns": list(SEQUENCE_FEATURES),
            "feature_means": data.feature_means.tolist(),
            "feature_stds": data.feature_stds.tolist(),
            "min_history": MIN_HISTORY,
        },
        model_path,
    )

    train_seconds = time.perf_counter() - started
    print_box(
        "LSTM RESULTS",
        [
            [
                f"Accounts with sequences:    {n_sequences:,}",
                f"Fraud-labelled sequences:    {n_fraud_sequences:,}",
                f"AUROC:                       {val_auroc:.3f}",
                f"Recall:                      {val_recall:.3f}",
                f"F1:                          {val_f1:.3f}",
                "Note: complements XGB via per-user behavioral drift",
                "Cold-start: falls back to XGB/IsoForest (<50 txns)",
                f"Inference latency (single): {latency_single:.0f}ms (CPU)",
                f"Model saved: ml/models/{MODEL_FILENAME}",
            ],
        ],
    )

    metrics = {
        "model": "lstm",
        "auroc": val_auroc,
        "recall": val_recall,
        "f1": val_f1,
        "fpr": val_fpr,
        "precision": float(tp / (tp + fp)) if (tp + fp) else 0.0,
        "final_loss": final_loss,
        "n_sequences": n_sequences,
        "n_fraud_sequences": n_fraud_sequences,
        "latency_single_ms": latency_single,
        "train_seconds": train_seconds,
        "path": str(model_path),
    }
    save_metrics("lstm", metrics)
    log_mlflow_run(
        EXPERIMENT_NAME,
        "lstm",
        params={
            "sequence_window": SEQUENCE_WINDOW,
            "min_history": MIN_HISTORY,
            "n_sequences": n_sequences,
            "epochs": epochs,
            "batch_size": BATCH_SIZE,
            "sequence_features": ",".join(SEQUENCE_FEATURES),
        },
        metrics={
            "final_loss": final_loss,
            "val_auroc": val_auroc,
            "val_recall": val_recall,
            "val_f1": val_f1,
        },
    )
    return metrics


def main() -> None:
    parser = argparse.ArgumentParser(description="Train LSTM sequence model")
    parser.add_argument("--feature-table", type=Path, default=DEFAULT_LABELED_TABLE)
    parser.add_argument("--epochs", type=int, default=EPOCHS)
    args = parser.parse_args()
    train_lstm(args.feature_table, epochs=args.epochs)


if __name__ == "__main__":
    main()
