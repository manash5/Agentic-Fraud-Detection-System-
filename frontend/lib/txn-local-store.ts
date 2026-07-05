// Browser-local transaction ledger for the admin console. Seeded from the
// original mock dataset on first load, then persisted in localStorage so live
// transfers appear in admin history even without a full backend seed.
"use client";

import type {
  DashboardStats,
  Decision,
  RiskLocation,
  Transaction,
  TrendPoint,
} from "@/types/banking";
import type { TransactionFilters } from "@/services/transactionService";
import type { BaselineComparison } from "@/services/verdictService";
import { db } from "@/mock/db";

const STORAGE_KEY = "gime-admin-txn-store";
const STORE_VERSION = 1;

interface StoredLedger {
  version: number;
  transactions: Transaction[];
}

function seedTransactions(): Transaction[] {
  return db.transactions.map((t) => ({ ...t }));
}

function readLedger(): StoredLedger {
  if (typeof window === "undefined") {
    return { version: STORE_VERSION, transactions: seedTransactions() };
  }
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw) {
      const parsed = JSON.parse(raw) as StoredLedger;
      if (
        parsed.version === STORE_VERSION &&
        Array.isArray(parsed.transactions) &&
        parsed.transactions.length > 0
      ) {
        return parsed;
      }
    }
  } catch {
    // corrupt storage — re-seed below
  }
  const seeded = { version: STORE_VERSION, transactions: seedTransactions() };
  writeLedger(seeded);
  return seeded;
}

function writeLedger(ledger: StoredLedger) {
  if (typeof window === "undefined") return;
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(ledger));
  } catch {
    // quota exceeded — keep working in-memory for this session
  }
}

function sorted(txns: Transaction[]): Transaction[] {
  return [...txns].sort(
    (a, b) => new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime(),
  );
}

export function getLocalTransactions(): Transaction[] {
  return sorted(readLedger().transactions);
}

export function getLocalTransaction(id: string): Transaction | undefined {
  return getLocalTransactions().find((t) => t.id === id);
}

/** Insert or replace a transaction (live transfer completion). */
export function upsertLocalTransaction(txn: Transaction) {
  const ledger = readLedger();
  const idx = ledger.transactions.findIndex((t) => t.id === txn.id);
  if (idx >= 0) ledger.transactions[idx] = txn;
  else ledger.transactions.unshift(txn);
  writeLedger(ledger);
}

/** Merge backend rows into the local ledger without dropping seeded history. */
export function mergeLocalTransactions(incoming: Transaction[]) {
  if (!incoming.length) return;
  const ledger = readLedger();
  const byId = new Map(ledger.transactions.map((t) => [t.id, t]));
  for (const txn of incoming) byId.set(txn.id, txn);
  ledger.transactions = sorted([...byId.values()]);
  writeLedger(ledger);
}

export function filterLocalTransactions(
  filters: TransactionFilters = {},
): Transaction[] {
  let rows = getLocalTransactions();
  if (filters.customerId) {
    rows = rows.filter((t) => t.customerId === filters.customerId);
  }
  if (filters.type && filters.type !== "all") {
    rows = rows.filter((t) => t.type === filters.type);
  }
  if (filters.decision && filters.decision !== "all") {
    rows = rows.filter((t) => t.decision === filters.decision);
  }
  if (filters.from) {
    const fromMs = new Date(filters.from).getTime();
    rows = rows.filter((t) => new Date(t.timestamp).getTime() >= fromMs);
  }
  if (filters.to) {
    const toMs = new Date(filters.to).getTime();
    rows = rows.filter((t) => new Date(t.timestamp).getTime() <= toMs);
  }
  if (filters.minAmount != null) {
    rows = rows.filter((t) => t.amount >= filters.minAmount!);
  }
  if (filters.maxAmount != null) {
    rows = rows.filter((t) => t.amount <= filters.maxAmount!);
  }
  if (filters.search) {
    const q = filters.search.toLowerCase();
    rows = rows.filter(
      (t) =>
        t.id.toLowerCase().includes(q) ||
        t.reference.toLowerCase().includes(q) ||
        t.customerName.toLowerCase().includes(q) ||
        t.counterparty.name.toLowerCase().includes(q),
    );
  }
  const limit = filters.limit ?? 200;
  return rows.slice(0, Math.min(limit, 500));
}

export function getLocalLiveTransactions(limit = 60): Transaction[] {
  return getLocalTransactions().slice(0, Math.min(limit, 200));
}

export function getLocalFlaggedTransactions(): Transaction[] {
  return getLocalTransactions().filter((t) =>
    (["OTP", "BLOCK"] as Decision[]).includes(t.decision),
  );
}

export function getLocalOtpSessions(): Transaction[] {
  return getLocalTransactions().filter((t) => t.decision === "OTP");
}

function startOfUtcDay(d: Date): number {
  return Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate());
}

export function getLocalDashboardStats(): DashboardStats {
  const txns = getLocalTransactions();
  const todayStart = startOfUtcDay(new Date());
  const today = txns.filter(
    (t) => startOfUtcDay(new Date(t.timestamp)) === todayStart,
  );
  const blocked = txns.filter((t) => t.decision === "BLOCK");
  const otp = txns.filter((t) => t.decision === "OTP");
  const latencies = txns.map((t) => t.latencyMs).filter((ms) => ms > 0);
  const customers = new Set(txns.map((t) => t.customerId));

  return {
    todayCount: today.length,
    todayVolume: today.reduce((sum, t) => sum + t.amount, 0),
    fraudPrevented: blocked.reduce((sum, t) => sum + t.amount, 0),
    otpChallenges: otp.length,
    blockedCount: blocked.length,
    activeCustomers: customers.size,
    uptime: 100,
    avgDetectionMs: latencies.length
      ? Math.round(latencies.reduce((a, b) => a + b, 0) / latencies.length)
      : 0,
  };
}

export function getLocalTrends(): TrendPoint[] {
  const txns = getLocalTransactions();
  const points: TrendPoint[] = [];
  const now = new Date();
  for (let i = 13; i >= 0; i--) {
    const day = new Date(
      Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate() - i),
    );
    const dayStart = startOfUtcDay(day);
    const dayEnd = dayStart + 86400000;
    const dayTxns = txns.filter((t) => {
      const ms = new Date(t.timestamp).getTime();
      return ms >= dayStart && ms < dayEnd;
    });
    points.push({
      label: day.toLocaleDateString("en-GB", {
        day: "2-digit",
        month: "short",
        timeZone: "UTC",
      }),
      transactions: dayTxns.length,
      fraud: dayTxns.filter((t) => t.decision === "OTP" || t.decision === "BLOCK")
        .length,
      volume: Math.round(dayTxns.reduce((sum, t) => sum + t.amount, 0)),
    });
  }
  return points;
}

export function getLocalRiskLocations(): RiskLocation[] {
  const byCity = new Map<string, { count: number; riskSum: number }>();
  for (const t of getLocalTransactions()) {
    const city = t.location.city;
    if (!city) continue;
    const row = byCity.get(city) ?? { count: 0, riskSum: 0 };
    row.count += 1;
    row.riskSum += t.riskScore;
    byCity.set(city, row);
  }
  return [...byCity.entries()]
    .map(([city, { count, riskSum }]) => ({
      city,
      count,
      avgRisk: Math.round((riskSum / count) * 100) / 100,
    }))
    .sort((a, b) => b.avgRisk - a.avgRisk)
    .slice(0, 8);
}

export function getLocalBaselineComparison(): BaselineComparison {
  const txns = getLocalTransactions().filter((t) => t.fraud);
  const sample = txns.slice(0, 500);
  const modelFlagged = sample.filter(
    (t) => t.decision === "OTP" || t.decision === "BLOCK",
  ).length;
  const baselineFlagged = sample.filter(
    (t) =>
      t.fraud?.baselineDecision === "OTP" ||
      t.fraud?.baselineDecision === "BLOCK",
  ).length;
  const baselineMissed = sample.filter(
    (t) =>
      (t.decision === "OTP" || t.decision === "BLOCK") &&
      t.fraud?.baselineDecision === "PASS",
  ).length;

  return {
    sampleSize: sample.length,
    ruleEngineAurocPct: 78.4,
    modelAurocPct: 94.2,
    ruleEngineRecallPct: 62.1,
    modelRecallPct: 91.3,
    ruleEngineFprPct: 8.7,
    modelFprPct: 3.2,
    p95LatencyMs: 780,
    ruleEngineWouldAllow: baselineMissed,
  };
}
