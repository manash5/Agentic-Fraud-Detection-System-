"use client";

import { useQuery } from "@tanstack/react-query";
import {
  getAdminCustomer,
  getAdminTransaction,
  getAdminTransactions,
  getAllAccounts,
  getAllCustomers,
  getDashboardStats,
  getFlaggedTransactions,
  getLiveTransactions,
  getOtpSessions,
  getRiskLocations,
  getSystemHealth,
  getTrends,
} from "@/services/adminService";
import { getBaselineComparison, getModelVerdict } from "@/services/verdictService";
import type { TransactionFilters } from "@/services/transactionService";

export function useAdminTransactions(filters: TransactionFilters = {}) {
  return useQuery({
    queryKey: ["admin", "transactions", filters],
    queryFn: () => getAdminTransactions(filters),
  });
}

export function useAdminTransaction(txnId?: string) {
  return useQuery({
    queryKey: ["admin", "transaction", txnId],
    queryFn: () => getAdminTransaction(txnId!),
    enabled: !!txnId,
  });
}

export function useAdminCustomer(customerId?: string) {
  return useQuery({
    queryKey: ["admin", "customer", customerId],
    queryFn: () => getAdminCustomer(customerId!),
    enabled: !!customerId,
  });
}

export function useDashboardStats() {
  return useQuery({ queryKey: ["admin", "stats"], queryFn: getDashboardStats });
}

export function useTrends() {
  return useQuery({ queryKey: ["admin", "trends"], queryFn: getTrends });
}

export function useRiskLocations() {
  return useQuery({
    queryKey: ["admin", "risk-locations"],
    queryFn: getRiskLocations,
  });
}

export function useLiveTransactions(limit?: number) {
  return useQuery({
    queryKey: ["admin", "live", limit],
    queryFn: () => getLiveTransactions(limit),
    refetchInterval: 8000,
  });
}

export function useFlaggedTransactions() {
  return useQuery({
    queryKey: ["admin", "flagged"],
    queryFn: getFlaggedTransactions,
  });
}

export function useAllCustomers() {
  return useQuery({
    queryKey: ["admin", "customers"],
    queryFn: getAllCustomers,
  });
}

export function useAllAccounts() {
  return useQuery({
    queryKey: ["admin", "accounts"],
    queryFn: getAllAccounts,
  });
}

export function useSystemHealth() {
  return useQuery({
    queryKey: ["admin", "health"],
    queryFn: getSystemHealth,
    refetchInterval: 10000,
  });
}

export function useOtpSessions() {
  return useQuery({ queryKey: ["admin", "otp"], queryFn: getOtpSessions });
}

export function useModelVerdict(txnId?: string) {
  return useQuery({
    queryKey: ["admin", "verdict", txnId],
    queryFn: () => getModelVerdict(txnId!),
    enabled: !!txnId,
  });
}

export function useBaselineComparison() {
  return useQuery({
    queryKey: ["admin", "baseline-comparison"],
    queryFn: getBaselineComparison,
  });
}
