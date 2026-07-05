import type {
  Account,
  Customer,
  DashboardStats,
  RiskLocation,
  SystemService,
  Transaction,
  TrendPoint,
} from "@/types/banking";
import {
  filterLocalTransactions,
  getLocalDashboardStats,
  getLocalFlaggedTransactions,
  getLocalLiveTransactions,
  getLocalOtpSessions,
  getLocalRiskLocations,
  getLocalTransaction,
  getLocalTrends,
  mergeLocalTransactions,
} from "@/lib/txn-local-store";
import { db } from "@/mock/db";
import type { TransactionFilters } from "./transactionService";
import { request } from "./http";

async function mergeAndReturnLive(limit: number): Promise<Transaction[]> {
  try {
    const remote = await request<Transaction[]>("/admin/live-transactions", {
      params: { limit },
    });
    mergeLocalTransactions(remote);
  } catch {
    // offline — local ledger only
  }
  return getLocalLiveTransactions(limit);
}

/** GET /admin/stats */
export async function getDashboardStats(): Promise<DashboardStats> {
  await mergeAndReturnLive(200);
  return getLocalDashboardStats();
}

/** GET /admin/trends */
export async function getTrends(): Promise<TrendPoint[]> {
  await mergeAndReturnLive(200);
  return getLocalTrends();
}

/** GET /admin/risk-locations */
export async function getRiskLocations(): Promise<RiskLocation[]> {
  await mergeAndReturnLive(200);
  return getLocalRiskLocations();
}

/** GET /admin/live-transactions */
export function getLiveTransactions(limit = 60): Promise<Transaction[]> {
  return mergeAndReturnLive(limit);
}

/** GET /admin/flagged */
export async function getFlaggedTransactions(): Promise<Transaction[]> {
  await mergeAndReturnLive(200);
  return getLocalFlaggedTransactions();
}

/** GET /admin/customers */
export function getAllCustomers(): Promise<Customer[]> {
  return request<Customer[]>("/admin/customers");
}

/** GET /admin/accounts */
export function getAllAccounts(): Promise<Account[]> {
  return request<Account[]>("/admin/accounts");
}

/** GET /admin/transactions — browse/filter (session-free, local-first). */
export function getAdminTransactions(
  filters: TransactionFilters = {},
): Promise<Transaction[]> {
  return mergeAndReturnLive(filters.limit ?? 200).then(() =>
    filterLocalTransactions(filters),
  );
}

/** GET /admin/transactions/:id — analyst-console transaction detail. */
export async function getAdminTransaction(id: string): Promise<Transaction> {
  try {
    const remote = await request<Transaction>(
      `/admin/transactions/${encodeURIComponent(id)}`,
    );
    mergeLocalTransactions([remote]);
    return remote;
  } catch (error) {
    const local = getLocalTransaction(id);
    if (local) return local;
    throw error;
  }
}

/** GET /admin/customers/:id — analyst-console customer detail. */
export async function getAdminCustomer(id: string): Promise<Customer> {
  try {
    return await request<Customer>(`/admin/customers/${encodeURIComponent(id)}`);
  } catch {
    const local = db.customers.find((c) => c.id === id);
    if (local) return local;
    throw new Error("Customer not found");
  }
}

/** GET /admin/system-health — live pings of every service/datastore. */
export function getSystemHealth(): Promise<SystemService[]> {
  return request<SystemService[]>("/admin/system-health");
}

/** GET /admin/otp-sessions — transactions with an OTP challenge. */
export async function getOtpSessions(): Promise<Transaction[]> {
  await mergeAndReturnLive(200);
  return getLocalOtpSessions();
}

export interface OtpEvent {
  id: number;
  txnId: string;
  accountId: string;
  mobile: string;
  channel: string;
  triggerReason: string | null;
  status: "SENT" | "VERIFIED" | "FAILED" | "EXPIRED" | "LOCKED";
  attempts: number;
  sentAt: string;
  verifiedAt: string | null;
}

/** GET /admin/otp-events — raw OTP challenge audit trail. */
export function getOtpEvents(): Promise<OtpEvent[]> {
  return request<OtpEvent[]>("/admin/otp-events");
}

export interface NetworkGraphData {
  collector: string;
  members: { id: string; transfers: number; total: number }[];
  neighbors: {
    id: string;
    direction: "in" | "out";
    transfers: number;
    is_fraud_seed: boolean;
  }[];
}

/** GET /admin/network-graph — COMM-042 ring + account neighborhood from Neo4j. */
export function getNetworkGraph(accountId?: string): Promise<NetworkGraphData> {
  return request<NetworkGraphData>("/admin/network-graph", {
    params: { accountId },
  });
}

export interface ThresholdSettings {
  otpThreshold: number;
  blockThreshold: number;
  disagreementThreshold: number;
}

/** GET /admin/settings — live decision thresholds. */
export function getAdminSettings(): Promise<ThresholdSettings> {
  return request<ThresholdSettings>("/admin/settings");
}

/** PUT /admin/settings — persist + apply thresholds to the live pipeline. */
export function saveAdminSettings(
  settings: ThresholdSettings,
): Promise<ThresholdSettings & { saved: boolean }> {
  return request<ThresholdSettings & { saved: boolean }>("/admin/settings", {
    method: "PUT",
    body: settings,
  });
}

/** GET /admin/reports/:key — downloads a live-data CSV report. */
export async function downloadReport(key: string): Promise<void> {
  const response = await fetch(`/api/admin/reports/${encodeURIComponent(key)}`);
  if (!response.ok) throw new Error(`Report failed (${response.status})`);
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `${key}-${new Date().toISOString().slice(0, 10)}.csv`;
  a.click();
  URL.revokeObjectURL(url);
}
