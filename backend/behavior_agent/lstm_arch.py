"""Two-branch LSTM architecture (mirror of the training notebook's Stage 8).

Kept in its own module so ``import torch`` can be deferred: on this macOS
setup, calling ``xgboost.load_model`` AFTER torch has been imported segfaults
(duplicate OpenMP runtimes). ``artifacts.load_bundle`` therefore loads the
XGBoost model first and only then imports this module — never import torch
(or this module) at the top of any behavior_agent module.
"""

from __future__ import annotations

import torch
import torch.nn as nn


class TwoBranchLSTM(nn.Module):
    """Sizes come from the checkpoint's ``arch`` dict, not hard-coded."""

    def __init__(self, n_seq_feat: int, n_static_feat: int, hidden: int,
                 layers: int, static_emb: int, fusion_hidden: int, dropout: float):
        super().__init__()
        self.lstm = nn.LSTM(n_seq_feat, hidden, num_layers=layers, batch_first=True)
        self.static = nn.Sequential(
            nn.Linear(n_static_feat, static_emb), nn.ReLU(), nn.Dropout(dropout))
        self.head = nn.Sequential(
            nn.Linear(hidden + static_emb, fusion_hidden), nn.ReLU(), nn.Dropout(dropout),
            nn.Linear(fusion_hidden, 1))

    def forward(self, seq: torch.Tensor, lengths: torch.Tensor,
                static: torch.Tensor) -> torch.Tensor:
        packed = nn.utils.rnn.pack_padded_sequence(
            seq, lengths.cpu(), batch_first=True, enforce_sorted=False)
        _, (h_n, _) = self.lstm(packed)
        fused = torch.cat([h_n[-1], self.static(static)], dim=1)
        return self.head(fused).squeeze(1)  # raw logits
