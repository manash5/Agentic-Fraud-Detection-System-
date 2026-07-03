import type { Account, Card, Customer } from "@/types/banking";
import { db } from "@/mock/db";
import { mockRequest } from "./http";

/** GET /customers/:id */
export function getCustomer(customerId: string): Promise<Customer> {
  return mockRequest(
    () => db.customers.find((c) => c.id === customerId)!,
    { min: 300, max: 900 },
  );
}

/** GET /accounts?customerId= */
export function getAccounts(customerId: string): Promise<Account[]> {
  return mockRequest(
    () => db.accounts.filter((a) => a.customerId === customerId),
    { min: 400, max: 1200 },
  );
}

/** GET /cards?customerId= */
export function getCards(customerId: string): Promise<Card[]> {
  return mockRequest(
    () => db.cards.filter((c) => c.customerId === customerId),
    { min: 400, max: 1000 },
  );
}
