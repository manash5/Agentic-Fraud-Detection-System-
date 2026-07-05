"use client";

// Shared phase derivation for the three transfer flows: maps the polled
// backend pipeline status onto the UI phase machine.
import * as React from "react";
import { toast } from "sonner";
import type { TransferStatus } from "@/services/transferService";
import type { Transaction } from "@/types/banking";
import type { TransferRun } from "./use-transfer-run";

export type TransferPhase =
  | "form"
  | "processing"
  | "otp"
  | "success"
  | "blocked";

export function useTransferPhases(run: TransferRun): {
  phase: TransferPhase;
  committed: Transaction | null;
} {
  const { txnId, status, verifiedTxn, reset } = run;

  const failReason = status?.status === "failed" ? status.failReason : null;
  React.useEffect(() => {
    if (failReason !== null) {
      toast.error(
        failReason === "otp_locked"
          ? "Transaction cancelled after too many incorrect codes."
          : "The transfer could not be processed. Please try again.",
      );
      reset();
    }
  }, [failReason, reset]);

  if (verifiedTxn) return { phase: "success", committed: verifiedTxn };
  if (!txnId) return { phase: "form", committed: null };
  switch (status?.status) {
    case "otp_pending":
      return { phase: "otp", committed: null };
    case "completed":
      return { phase: "success", committed: status.txn };
    case "blocked":
      return { phase: "blocked", committed: null };
    case "failed":
      return { phase: "form", committed: null };
    default:
      return { phase: "processing", committed: null };
  }
}

/** Track-B trigger code shown on the OTP screen, from the live fraud result. */
export function otpTriggerReason(
  status: TransferStatus | null,
): string | undefined {
  const analysis = status?.fraud?.analysis;
  if (!analysis) return undefined;
  if (analysis.synthesis.disagreement >= 0.04)
    return "AGENT_DISAGREEMENT_FORCE_OTP";
  const top = [...analysis.agents].sort((a, b) => b.risk - a.risk)[0];
  if (!top) return undefined;
  switch (top.agent) {
    case "geo":
      return "IMPOSSIBLE_TRAVEL_DETECTED";
    case "velocity":
      return "VELOCITY_THRESHOLD_EXCEEDED";
    case "graph":
      return "FRAUD_RING_PROXIMITY";
    default:
      return "BEHAVIOURAL_ANOMALY_DETECTED";
  }
}
