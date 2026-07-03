"""Detect the COMM-042 smurfing ring in the account graph.

Method: pick the collector (fraud-seed node with the highest in-degree), then
BFS backwards up to 2 hops over edges carrying structuring-pattern amounts
(just below the NRB reporting thresholds 9,999 / 49,999 / 99,999 NPR).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import networkx as nx
import pandas as pd

from ml.training.common import BACKEND_ROOT, MODELS_DIR

NODES_PATH = BACKEND_ROOT / "datasets_processed" / "account_graph_nodes.csv"
EDGES_PATH = BACKEND_ROOT / "datasets_processed" / "account_graph_edges.csv"
OUTPUT_FILENAME = "community_detection.json"

STRUCTURING_THRESHOLDS = (9_999, 49_999, 99_999)
STRUCTURING_TOLERANCE = 600.0
MAX_HOPS = 2


def _is_structuring(amount: pd.Series) -> pd.Series:
    amt = pd.to_numeric(amount, errors="coerce")
    mask = pd.Series(False, index=amt.index)
    for threshold in STRUCTURING_THRESHOLDS:
        mask |= (amt - threshold).abs() <= STRUCTURING_TOLERANCE
    return mask.fillna(False)


def detect_community(
    nodes_path: Path = NODES_PATH, edges_path: Path = EDGES_PATH
) -> dict:
    nodes = pd.read_csv(nodes_path)
    edges = pd.read_csv(edges_path)

    seeds = nodes[nodes["is_fraud_seed"].astype(str).str.lower().isin(["true", "1"])]
    if seeds.empty:
        raise ValueError("No is_fraud_seed=True nodes found in account graph")
    collector_row = seeds.loc[seeds["degree_in"].idxmax()]
    collector = str(collector_row["id"])
    collector_degree_in = int(collector_row["degree_in"])

    struct_edges = edges[_is_structuring(edges["amount_npr"])]

    # BFS backwards from the collector over structuring edges (≤ 2 hops).
    graph = nx.DiGraph()
    graph.add_edges_from(zip(struct_edges["source"], struct_edges["target"]))
    if collector in graph:
        reversed_graph = graph.reverse(copy=False)
        hop_distances = nx.single_source_shortest_path_length(
            reversed_graph, collector, cutoff=MAX_HOPS
        )
    else:
        hop_distances = {}
    ring_members = sorted(node for node, dist in hop_distances.items() if dist >= 1)

    to_collector = struct_edges[struct_edges["target"] == collector]
    num_transfers = int(len(to_collector))
    total_funneled = float(pd.to_numeric(to_collector["amount_npr"], errors="coerce").sum())
    direct_senders = sorted(to_collector["source"].astype(str).unique().tolist())

    result = {
        "community_id": "COMM-042",
        "collector_account": collector,
        "collector_degree_in": collector_degree_in,
        "ring_members": ring_members,
        "direct_structuring_senders": direct_senders,
        "detection_method": (
            "BFS from fraud_seed collector (highest degree_in) over "
            "structuring-amount edges (9999/49999/99999 ± 600 NPR), ≤ 2 hops"
        ),
        "total_amount_funneled_npr": round(total_funneled, 2),
        "num_structuring_transfers": num_transfers,
    }

    output_path = MODELS_DIR / OUTPUT_FILENAME
    output_path.write_text(json.dumps(result, indent=2), encoding="utf-8")

    print("✓ COMM-042 Smurfing Ring Detected")
    print(f"├─ Collector: {collector} (degree_in={collector_degree_in:,})")
    print(f"├─ Ring members found: {len(ring_members)} accounts (≤{MAX_HOPS} hops)")
    print(f"├─ Direct structuring senders: {len(direct_senders)}")
    print(f"├─ Structuring transfers: {num_transfers:,}")
    print(f"└─ Total funneled: NPR {total_funneled:,.0f}")
    print(f"✓ Saved: ml/models/{OUTPUT_FILENAME} (for hackathon bonus submission)")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Detect the COMM-042 smurfing community")
    parser.add_argument("--nodes", type=Path, default=NODES_PATH)
    parser.add_argument("--edges", type=Path, default=EDGES_PATH)
    args = parser.parse_args()
    detect_community(args.nodes, args.edges)


if __name__ == "__main__":
    main()
