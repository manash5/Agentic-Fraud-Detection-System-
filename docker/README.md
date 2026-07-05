# Docker deployment

Containerized full stack for the Agentic Fraud Detection System. Everything runs
on an internal Docker network; **exactly two ports are reachable from the host.**

## Quick start

```bash
cp docker/.env.example docker/.env          # then edit POSTGRES_PASSWORD + NEO4J_PASSWORD
docker compose -f docker/docker-compose.yml up --build
```

First build is heavy (the backend image includes the full ML stack — torch,
xgboost, lightgbm, shap). Once up:

- Frontend: <http://localhost:3000>
- Backend health: <http://localhost:8000/health>

Stop: `docker compose -f docker/docker-compose.yml down`
Reset all data (drop volumes — see "Resetting" below): `... down -v`

## The two exposed ports (and why)

| Service | Host port | Why exposed |
|---|---|---|
| **frontend** | `3000` | The Next.js app the user opens in a browser. |
| **backend**  | `8000` | The FastAPI agents API. The frontend proxies `/api/*` to it; also handy for `curl`-ing `/health`. |

Everything else is **internal-only** (no `ports:` mapping) and reachable only by
other containers via the `fraud_net` network, by service name:

| Service | Internal address | Role |
|---|---|---|
| postgres | `postgres:5432` | app + agent reference data |
| redis | `redis:6379` | velocity/geo caches, sessions, OTP, txn workflow state |
| neo4j | `neo4j:7687` | graph agent's account network |
| kafka | `kafka:9092` | async pipeline event bus |
| orchestrator | *(none)* | Kafka consumer that runs the agent pipeline |

> The task brief named Postgres + Redis. The backend's agents also require
> **Neo4j** (graph agent) and **Kafka** (the orchestrator driving the async
> pipeline), so those are included here — as internal-only services, preserving
> the exactly-two-host-ports rule. Verify with `docker compose ... ps` /
> `... port`: only `frontend` and `backend` list a host port.

## How cross-service networking is wired

The app was written assuming `localhost`. In containers that breaks — each
container's `localhost` is itself. The compose file overrides every connection
target with the **docker service name** (this is the single most common
dockerization bug):

- `FRAUD_DB_DSN = postgresql://…@postgres:5432/fraud_detection_global`
- `FRAUD_REDIS_HOST = redis`
- `FRAUD_KAFKA_BOOTSTRAP = kafka:9092`
- `NEO4J_URI = bolt://neo4j:7687`

`DATABASE_URL` / `REDIS_URL` are also set (documentation/compat), but the code
reads the `FRAUD_*` / `NEO4J_*` variables. The backend binds `--host 0.0.0.0`
(not localhost) so it's reachable across the network. The frontend's `/api/*`
rewrite target is baked to `http://backend:8000` at build time.

## Overriding env vars

All configuration lives in `docker/.env` (copied from `.env.example`). Common ones:

- `POSTGRES_PASSWORD`, `NEO4J_PASSWORD` — **required**, no defaults.
- `BACKEND_PORT`, `FRONTEND_PORT` — change the host-side port if 8000/3000 are taken
  (e.g. `FRONTEND_PORT=3001`); the container-internal ports stay fixed.
- `OTP_DEV_MODE=1` (default) logs the OTP and returns it as `devCode` in the
  transfer status (no SMS account needed). Set `0` + `EASYSENDSMS_API_KEY` for real SMS.

## Resetting Postgres (schema changes during development)

`docker/postgres/init.sql` only runs on the **first** boot of an empty data
volume. After changing the schema, drop the volume so it re-runs:

```bash
docker compose -f docker/docker-compose.yml down -v   # drops postgres_data, redis_data, neo4j_data
docker compose -f docker/docker-compose.yml up --build
```

Regenerate `init.sql` from a populated local DB:

```bash
pg_dump -d fraud_detection_global --schema-only --no-owner --no-privileges \
  > docker/postgres/init.sql   # then re-add the header comment
```

## Loading reference data (required for real fraud decisions)

`init.sql` creates the **schema** (all 21 tables) but not the data. A fresh
stack starts with empty Postgres/Neo4j, so the agents run but have no history to
score against (the backend still starts and `/health` responds; agents lacking
data report `unavailable`/`not_found`). To get real end-to-end decisions, load
the reference data and seed the demo profiles **after the stack is up**:

```bash
# 1. Load the dataset CSVs into Postgres + the Neo4j graph (loaders live in
#    backend/scripts and backend/feature_engineering). The datasets/ dir is NOT
#    baked into the image — bind-mount it for the load, e.g.:
docker compose -f docker/docker-compose.yml run --rm \
  -v "$(pwd)/backend/datasets:/app/datasets:ro" \
  backend python -m scripts.load_device_fingerprints
#    (repeat for the other loaders / your DB import of transactions_raw etc.)

# 2. Seed the app tables + demo login profiles:
docker compose -f docker/docker-compose.yml exec backend python -m scripts.seed_app_data
docker compose -f docker/docker-compose.yml exec backend python -m scripts.seed_demo_profiles
```

The heavy `datasets/` (~1.3 GB) and `datasets_processed/` (~786 MB) directories
are intentionally excluded from the image (see the `*.dockerignore` files) and
mounted only when loading.

## Verifying the setup

```bash
docker compose -f docker/docker-compose.yml ps            # all services healthy
docker compose -f docker/docker-compose.yml port frontend # -> 0.0.0.0:3000
docker compose -f docker/docker-compose.yml port backend  # -> 0.0.0.0:8000
docker compose -f docker/docker-compose.yml port postgres # -> (no output: not exposed)
docker compose -f docker/docker-compose.yml port redis    # -> (no output: not exposed)

curl -fsS http://localhost:8000/health                    # backend reachable from host
curl -fsS http://localhost:3000/login -o /dev/null -w '%{http_code}\n'   # frontend reachable

# Prove Postgres/Redis are NOT exposed to the host (both should FAIL/refuse):
nc -z localhost 5432 && echo "EXPOSED (bad)" || echo "postgres not reachable (correct)"
nc -z localhost 6379 && echo "EXPOSED (bad)" || echo "redis not reachable (correct)"
```

## Files

```
docker/
├── docker-compose.yml               # the stack; only frontend+backend get host ports
├── backend.Dockerfile               # uv + ML deps; uvicorn --host 0.0.0.0
├── backend.Dockerfile.dockerignore  # keep the backend image lean (no datasets)
├── frontend.Dockerfile              # Next.js standalone production server
├── frontend.Dockerfile.dockerignore # exclude backend tree + node_modules
├── postgres/
│   └── init.sql                     # schema, auto-run on first DB boot
├── .env.example                     # documented vars (copy to .env; .env is gitignored)
└── README.md
```
