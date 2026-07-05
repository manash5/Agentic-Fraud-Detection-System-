// Live transfer pipeline client. A submit returns 202 immediately; the
// backend pushes the transaction through Kafka -> agents -> synthesis, and
// the frontend polls getTransferStatus() for agent-by-agent progress, the
// final decision, OTP requirements, and the committed transaction.
import type { FraudResult, Transaction, TransferRequest } from "@/types/banking";
import { request } from "./http";

export interface SubmitTransferResponse {
  txnId: string;
  reference: string;
  status: "processing";
}

export interface AgentProgress {
  status: "ok" | "unavailable" | "not_found" | "skipped" | "error";
  risk_score: number | null;
  confidence: number | null;
  latency_ms: number | null;
  explanation?: unknown;
  detail?: string | null;
}

export interface OtpInfo {
  expiresAt: string;
  attemptsLeft: number;
  channel: string;
  ttlSeconds?: number;
  devCode?: string;
  smsFailed?: boolean;
}

export type TransferStage =
  | "processing"
  | "otp_pending"
  | "completed"
  | "blocked"
  | "failed";

export interface TransferStatus {
  txnId: string;
  status: TransferStage;
  agents: Partial<
    Record<"velocity" | "geo" | "graph" | "behavior", AgentProgress>
  >;
  synthesis: {
    final_score: number;
    fraud_pattern: string;
    disagreement_score: number;
    agents_used: string[];
    weights_applied: Record<string, number>;
  } | null;
  decision: "PASS" | "OTP" | "BLOCK" | null;
  fraud: FraudResult | null;
  txn: Transaction | null;
  otp: OtpInfo | null;
  failReason?: string | null;
}

/** POST /transfer — persists + publishes to Kafka; 202 with the txn id. */
export function submitTransfer(
  req: TransferRequest,
): Promise<SubmitTransferResponse> {
  return request<SubmitTransferResponse>("/transfer", {
    method: "POST",
    body: req,
  });
}

/** GET /transfers/:id/status — live pipeline state (poll until terminal). */
export function getTransferStatus(txnId: string): Promise<TransferStatus> {
  return request<TransferStatus>(
    `/transfers/${encodeURIComponent(txnId)}/status`,
  );
}

/** POST /otp/verify — validates the SMS code; completes the transaction. */
export function verifyTransferOtp(
  txnId: string,
  code: string,
): Promise<{ verified: boolean; txn: Transaction }> {
  return request<{ verified: boolean; txn: Transaction }>("/otp/verify", {
    method: "POST",
    body: { txnId, code },
  });
}

/** POST /otp/resend — re-issues the OTP (rate limited). */
export function resendTransferOtp(
  txnId: string,
): Promise<{ sent: boolean; otp: OtpInfo }> {
  return request<{ sent: boolean; otp: OtpInfo }>("/otp/resend", {
    method: "POST",
    body: { txnId },
  });
}
