import type { Transaction, TransactionType } from "@/types/banking";
import { request } from "./http";

export interface TransactionFilters {
  customerId?: string;
  search?: string;
  type?: TransactionType | "all";
  decision?: string;
  from?: string;
  to?: string;
  minAmount?: number;
  maxAmount?: number;
  limit?: number;
}

/** GET /transactions */
export function getTransactions(
  filters: TransactionFilters = {},
): Promise<Transaction[]> {
  return request<Transaction[]>("/transactions", {
    params: {
      customerId: filters.customerId,
      search: filters.search,
      type: filters.type,
      decision: filters.decision,
      from: filters.from,
      to: filters.to,
      minAmount: filters.minAmount,
      maxAmount: filters.maxAmount,
      limit: filters.limit,
    },
  });
}

/** GET /transactions/:id */
export function getTransaction(id: string): Promise<Transaction> {
  return request<Transaction>(`/transactions/${encodeURIComponent(id)}`);
}
