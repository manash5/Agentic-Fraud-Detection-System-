"""Idempotent DDL for the app-layer tables (run at API startup and by the seed).

These tables serve the banking frontend (customers/accounts/cards/ledger);
the agent reference tables (transactions_raw, customer_profiles, ...) stay
dataset-owned. The one exception is the `source` column added to
transactions_raw so live transactions can be told apart from dataset rows
(cleanup: DELETE FROM transactions_raw WHERE source='live').
"""

from __future__ import annotations

_DDL = """
CREATE TABLE IF NOT EXISTS app_users (
    id            BIGSERIAL PRIMARY KEY,
    customer_id   TEXT NOT NULL UNIQUE,
    mobile        TEXT NOT NULL UNIQUE,
    mpin_hash     TEXT NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS app_customers (
    id                          TEXT PRIMARY KEY,          -- CUST-...
    agent_account_id            TEXT NOT NULL,             -- customer_profiles.account_id
    name                        TEXT NOT NULL,
    gender                      TEXT NOT NULL,
    account_number              TEXT NOT NULL,
    mobile                      TEXT NOT NULL,
    email                       TEXT NOT NULL,
    address                     TEXT NOT NULL,
    city                        TEXT NOT NULL,
    kyc_status                  TEXT NOT NULL,
    risk_level                  TEXT NOT NULL,
    joined_at                   TIMESTAMPTZ NOT NULL,
    avatar_color                TEXT NOT NULL,
    citizenship_no              TEXT NOT NULL,
    branch                      TEXT NOT NULL,
    district                    TEXT NOT NULL,
    province                    TEXT NOT NULL,
    kyc_tier                    TEXT NOT NULL,
    is_dormant                  BOOLEAN NOT NULL DEFAULT FALSE,
    num_beneficiaries_registered INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_app_customers_mobile ON app_customers (mobile);
CREATE INDEX IF NOT EXISTS idx_app_customers_acct ON app_customers (account_number);

CREATE TABLE IF NOT EXISTS app_accounts (
    id             TEXT PRIMARY KEY,      -- agent-space id (ACC-...): what the pipeline scores
    customer_id    TEXT NOT NULL REFERENCES app_customers(id),
    type           TEXT NOT NULL,
    name           TEXT NOT NULL,
    account_number TEXT NOT NULL,
    balance        NUMERIC(14,2) NOT NULL,
    currency       TEXT NOT NULL DEFAULT 'NPR',
    status         TEXT NOT NULL DEFAULT 'active',
    interest_rate  DOUBLE PRECISION NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_app_accounts_customer ON app_accounts (customer_id);

CREATE TABLE IF NOT EXISTS app_cards (
    id           TEXT PRIMARY KEY,
    customer_id  TEXT NOT NULL REFERENCES app_customers(id),
    type         TEXT NOT NULL,
    scheme       TEXT NOT NULL,
    number       TEXT NOT NULL,
    holder       TEXT NOT NULL,
    expiry       TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'active',
    card_limit   NUMERIC(14,2) NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_app_cards_customer ON app_cards (customer_id);

CREATE TABLE IF NOT EXISTS app_transactions (
    id                    TEXT PRIMARY KEY,     -- txn_id, shared with transactions_raw
    reference             TEXT NOT NULL,
    customer_id           TEXT NOT NULL,
    customer_name         TEXT NOT NULL,
    account_id            TEXT NOT NULL,        -- app_accounts.id / agent account id
    account_number        TEXT NOT NULL,
    cp_name               TEXT NOT NULL,
    cp_account            TEXT NOT NULL,
    cp_bank               TEXT NOT NULL,
    cp_is_wallet          BOOLEAN NOT NULL DEFAULT FALSE,
    amount                NUMERIC(14,2) NOT NULL,
    direction             TEXT NOT NULL,
    type                  TEXT NOT NULL,
    channel               TEXT NOT NULL,
    status                TEXT NOT NULL,
    decision              TEXT,
    risk_score            DOUBLE PRECISION,
    latency_ms            DOUBLE PRECISION,
    location_city         TEXT,
    location_lat          DOUBLE PRECISION,
    location_lng          DOUBLE PRECISION,
    device                TEXT,
    ip_address            TEXT,
    remarks               TEXT,
    ts                    TIMESTAMPTZ NOT NULL,
    txn_type              TEXT NOT NULL,
    counterparty_id       TEXT,
    fraud_type            TEXT,
    auth_method           TEXT,
    mcc                   TEXT,
    is_vpn                BOOLEAN NOT NULL DEFAULT FALSE,
    is_tor                BOOLEAN NOT NULL DEFAULT FALSE,
    impossible_travel     BOOLEAN NOT NULL DEFAULT FALSE,
    prev_txn_km           DOUBLE PRECISION,
    prev_txn_delta_min    DOUBLE PRECISION,
    z_score_amount        DOUBLE PRECISION,
    txn_count_1m          INTEGER,
    dormancy_break        BOOLEAN NOT NULL DEFAULT FALSE,
    night_flag            BOOLEAN NOT NULL DEFAULT FALSE,
    new_counterparty_flag BOOLEAN NOT NULL DEFAULT FALSE,
    device_id             TEXT,
    fraud                 JSONB
);
CREATE INDEX IF NOT EXISTS idx_app_txn_customer_ts ON app_transactions (customer_id, ts DESC);
CREATE INDEX IF NOT EXISTS idx_app_txn_ts ON app_transactions (ts DESC);
CREATE INDEX IF NOT EXISTS idx_app_txn_decision ON app_transactions (decision);

CREATE TABLE IF NOT EXISTS app_otp_events (
    id             BIGSERIAL PRIMARY KEY,
    txn_id         TEXT NOT NULL,
    account_id     TEXT NOT NULL,
    mobile         TEXT NOT NULL,
    channel        TEXT NOT NULL DEFAULT 'SMS',
    trigger_reason TEXT,
    status         TEXT NOT NULL,          -- SENT | VERIFIED | FAILED | EXPIRED | LOCKED
    attempts       INTEGER NOT NULL DEFAULT 0,
    sent_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    verified_at    TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_app_otp_txn ON app_otp_events (txn_id);

CREATE TABLE IF NOT EXISTS app_settings (
    key        TEXT PRIMARY KEY,
    value      JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE transactions_raw ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'dataset';
"""


async def ensure_schema(conn) -> None:
    await conn.execute(_DDL)
