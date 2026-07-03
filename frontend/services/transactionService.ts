import type { Transaction, TransactionType } from "@/types/banking";
import { db } from "@/mock/db";
import { mockRequest } from "./http";

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
  return mockRequest(() => {
    let rows = [...db.transactions];
    if (filters.customerId)
      rows = rows.filter((t) => t.customerId === filters.customerId);
    if (filters.type && filters.type !== "all")
      rows = rows.filter((t) => t.type === filters.type);
    if (filters.decision && filters.decision !== "all")
      rows = rows.filter((t) => t.decision === filters.decision);
    if (filters.from)
      rows = rows.filter(
        (t) => new Date(t.timestamp) >= new Date(filters.from!),
      );
    if (filters.to)
      rows = rows.filter(
        (t) => new Date(t.timestamp) <= new Date(filters.to!),
      );
    if (typeof filters.minAmount === "number")
      rows = rows.filter((t) => t.amount >= filters.minAmount!);
    if (typeof filters.maxAmount === "number")
      rows = rows.filter((t) => t.amount <= filters.maxAmount!);
    if (filters.search) {
      const q = filters.search.toLowerCase();
      rows = rows.filter(
        (t) =>
          t.reference.toLowerCase().includes(q) ||
          t.id.toLowerCase().includes(q) ||
          t.counterparty.name.toLowerCase().includes(q) ||
          t.customerName.toLowerCase().includes(q),
      );
    }
    return filters.limit ? rows.slice(0, filters.limit) : rows;
  });
}

/** GET /transactions/:id */
export function getTransaction(id: string): Promise<Transaction | undefined> {
  return mockRequest(() => db.transactions.find((t) => t.id === id), {
    min: 300,
    max: 700,
  });
}
