import type { DemoProfile } from "@/lib/auth";

/**
 * Builds the /transfer/bank href that pre-populates the transfer form with a
 * demo profile's corresponding transaction. The bank flow reads these params.
 */
export function buildTransferPrefillHref(prefill: DemoProfile["prefill"]): string {
  const params = new URLSearchParams({
    from: prefill.fromAccountId,
    destination: prefill.destination,
    account: prefill.recipientAccount,
    name: prefill.recipientName,
    bank: prefill.recipientBank,
    amount: String(prefill.amount),
    remarks: prefill.remarks,
    prefill: "1",
  });
  return `/transfer/bank?${params.toString()}`;
}
