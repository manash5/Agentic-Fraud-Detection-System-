import { db } from "@/mock/db";
import { BANKS, LAST_NAMES, MALE_FIRST_NAMES, FEMALE_FIRST_NAMES } from "@/mock/constants";
import { ApiError, mockRequest } from "./http";
import type { TransferDestination } from "@/types/banking";

export interface ResolvedRecipient {
  accountNumber: string;
  name: string;
  bank: string;
}

/**
 * GET /recipients/resolve — fetch the account holder name for an account number.
 * Mimics the "name auto-fetch" of real banking apps.
 */
export function resolveRecipient(
  accountNumber: string,
  destination: TransferDestination,
  bank?: string,
): Promise<ResolvedRecipient> {
  return mockRequest(
    () => {
      const clean = accountNumber.trim();
      if (clean.length < 6) {
        throw new ApiError("Account number not found.", 404);
      }
      // If it matches a real customer in our bank, use their name.
      const existing = db.customers.find((c) => c.accountNumber === clean);
      const seed = clean
        .split("")
        .reduce((a, ch) => a + ch.charCodeAt(0), 0);
      const pool =
        seed % 2 === 0 ? MALE_FIRST_NAMES : FEMALE_FIRST_NAMES;
      const name =
        existing?.name ??
        `${pool[seed % pool.length]} ${LAST_NAMES[seed % LAST_NAMES.length]}`;
      const resolvedBank =
        destination === "global_ime" || destination === "own"
          ? "Global IME Bank"
          : bank || BANKS[seed % BANKS.length];
      return { accountNumber: clean, name, bank: resolvedBank };
    },
    { min: 700, max: 1600 },
  );
}
