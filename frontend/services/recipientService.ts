import type { TransferDestination } from "@/types/banking";
import { request } from "./http";

export interface ResolvedRecipient {
  accountNumber: string;
  name: string;
  bank: string;
}

/** GET /recipients/resolve — account-holder name auto-fetch. */
export function resolveRecipient(
  accountNumber: string,
  destination: TransferDestination,
  bank?: string,
): Promise<ResolvedRecipient> {
  return request<ResolvedRecipient>("/recipients/resolve", {
    params: { account: accountNumber.trim(), destination, bank },
  });
}
