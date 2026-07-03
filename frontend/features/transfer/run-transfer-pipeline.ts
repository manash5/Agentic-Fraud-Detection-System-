import { commitTransfer, fraudCheck } from "@/services/transferService";
import type { FraudResult, Transaction, TransferRequest } from "@/types/banking";

export type TransferPipelineOutcome =
  | { kind: "blocked"; fraud: FraudResult }
  | { kind: "otp"; fraud: FraudResult }
  | { kind: "success"; fraud: FraudResult; txn: Transaction };

/** Runs fraud check then commits or branches to OTP / block — no UI stages. */
export async function runTransferPipeline(
  request: TransferRequest,
): Promise<TransferPipelineOutcome> {
  const fraud = await fraudCheck(request);
  if (fraud.decision === "BLOCK") return { kind: "blocked", fraud };
  if (fraud.decision === "OTP") return { kind: "otp", fraud };
  const txn = await commitTransfer(request, fraud);
  return { kind: "success", fraud, txn };
}
