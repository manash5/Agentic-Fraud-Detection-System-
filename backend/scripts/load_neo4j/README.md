# Neo4j Loader

Loads processed fraud detection graph data into a local Neo4j Desktop database.

The script reads:

- `datasets_processed/account_graph_nodes.csv`
- `datasets_processed/account_graph_edges.csv`
- `datasets/comm042_ring_members.json`

It creates `Account` nodes, directed `TRANSFER` relationships, marks COMM-042 ring accounts as fraud seeds, and prints summary statistics at the end.

## Setup

Ensure Neo4j Desktop is running, then add the connection details to `backend/.env`:

```env
NEO4J_URI=bolt://localhost:7687
NEO4J_USERNAME=neo4j
NEO4J_PASSWORD=your-password
NEO4J_DATABASE=fraud-detection
```

`NEO4J_DATABASE` is optional when your Neo4j DBMS has a default database configured. Set it when Neo4j Desktop uses a custom database name.

Install the Neo4j Python driver if it is not already available:

```bash
pip install neo4j
```

## Run

From the `backend` directory:

```bash
python -m scripts.load_neo4j.loader
```

You can also choose the database at runtime:

```bash
python -m scripts.load_neo4j.loader --database fraud-detection
```

By default, the loader does not delete existing graph data. It uses `MERGE` to overwrite matching `Account` nodes and matching `TRANSFER` relationships, so it is safe to re-run.

To clear existing `Account` nodes and their relationships before loading:

```bash
python -m scripts.load_neo4j.loader --clear
```

The clear operation asks for confirmation. For non-interactive runs:

```bash
python -m scripts.load_neo4j.loader --clear --yes
```

## Idempotency

The loader creates a uniqueness constraint for `Account.id`:

```cypher
CREATE CONSTRAINT account_id_unique IF NOT EXISTS
FOR (a:Account) REQUIRE a.id IS UNIQUE
```

Account nodes are loaded with:

```cypher
MERGE (a:Account {id: row.id})
SET a.type = row.type,
    a.risk_tier = row.risk_tier,
    a.kyc_tier = row.kyc_tier,
    a.degree_in = row.degree_in,
    a.degree_out = row.degree_out,
    a.total_received_npr = row.total_received_npr,
    a.total_sent_npr = row.total_sent_npr,
    a.is_fraud_seed = row.is_fraud_seed
```

Transfer relationships are keyed by transaction id between source and target accounts:

```cypher
MATCH (source:Account {id: row.source})
MATCH (target:Account {id: row.target})
MERGE (source)-[r:TRANSFER {txn_id: row.txn_id}]->(target)
SET r.amount_npr = row.amount_npr,
    r.timestamp = row.timestamp,
    r.txn_type = row.txn_type,
    r.edge_weight = row.edge_weight,
    r.is_first_transfer_to_target = row.is_first_transfer_to_target,
    r.within_24h_reciprocal = row.within_24h_reciprocal
```

COMM-042 ring accounts are marked with:

```cypher
MATCH (a:Account)
WHERE a.id IN $accounts
SET a.is_fraud_seed = true,
    a.community_id = "COMM-042"
```
