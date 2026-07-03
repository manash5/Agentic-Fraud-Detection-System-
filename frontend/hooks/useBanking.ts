"use client";

import { useQuery } from "@tanstack/react-query";
import {
  getAccounts,
  getCards,
  getCustomer,
} from "@/services/accountService";
import {
  getTransaction,
  getTransactions,
  type TransactionFilters,
} from "@/services/transactionService";

export function useCustomer(customerId?: string) {
  return useQuery({
    queryKey: ["customer", customerId],
    queryFn: () => getCustomer(customerId!),
    enabled: !!customerId,
  });
}

export function useAccounts(customerId?: string) {
  return useQuery({
    queryKey: ["accounts", customerId],
    queryFn: () => getAccounts(customerId!),
    enabled: !!customerId,
  });
}

export function useCards(customerId?: string) {
  return useQuery({
    queryKey: ["cards", customerId],
    queryFn: () => getCards(customerId!),
    enabled: !!customerId,
  });
}

export function useTransactions(filters: TransactionFilters = {}) {
  return useQuery({
    queryKey: ["transactions", filters],
    queryFn: () => getTransactions(filters),
  });
}

export function useTransaction(id?: string) {
  return useQuery({
    queryKey: ["transaction", id],
    queryFn: () => getTransaction(id!),
    enabled: !!id,
  });
}
