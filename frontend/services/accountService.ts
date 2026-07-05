import type { Account, Card, Customer } from "@/types/banking";
import { request } from "./http";

/** GET /customers/:id */
export function getCustomer(customerId: string): Promise<Customer> {
  return request<Customer>(`/customers/${encodeURIComponent(customerId)}`);
}

/** GET /accounts?customerId= */
export function getAccounts(customerId: string): Promise<Account[]> {
  return request<Account[]>("/accounts", { params: { customerId } });
}

/** GET /cards?customerId= */
export function getCards(customerId: string): Promise<Card[]> {
  return request<Card[]>("/cards", { params: { customerId } });
}
