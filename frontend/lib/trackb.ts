// Display + mapping helpers that translate the internal banking-simulation
// model (types/banking.ts) into the exact terminology and formats used by
// the GIBL Hackathon 2026 Track B data manual, so admins/judges see the
// hackathon's own vocabulary without the rest of the app needing to change.
import type { Decision, Transaction, TransactionType, TransferDestination } from "@/types/banking";
import type {
  AgentFlag,
  AgentName,
  Channel,
  FraudDecision,
  FraudType,
  ModelVerdict,
  TxnType,
} from "@/types/trackb";

export function toTrackBDecision(
  decision: Decision,
  forcedByDisagreement = false,
): FraudDecision {
  if (decision === "BLOCK") return forcedByDisagreement ? "BLOCK_AND_OTP" : "BLOCK";
  if (decision === "OTP") return "OTP_ONLY";
  return "ALLOW";
}

export const trackBDecisionMeta: Record<
  FraudDecision,
  { label: string; variant: "success" | "warning" | "destructive"; dot: string }
> = {
  ALLOW: { label: "Allow", variant: "success", dot: "bg-success" },
  OTP_ONLY: { label: "OTP Only", variant: "warning", dot: "bg-warning" },
  BLOCK: { label: "Block", variant: "destructive", dot: "bg-destructive" },
  BLOCK_AND_OTP: { label: "Block + OTP", variant: "destructive", dot: "bg-destructive" },
};

export const txnTypeLabels: Record<TxnType, string> = {
  ESEWA_P2P: "eSewa P2P Transfer",
  CARD_POS: "Card POS Payment",
  ATM_WITHDRAWAL: "ATM Withdrawal",
  SWIFT_OUTWARD: "SWIFT Outward Remittance",
  KHALTI_QR: "Khalti QR Payment",
  RTGS: "RTGS Bank Transfer",
  MOBILE_TOPUP: "Mobile Top-up",
  UTILITY_BILL: "Utility Bill Payment",
};

export const channelLabels: Record<Channel, string> = {
  MOBILE_APP: "Mobile App",
  WEB: "Web Banking",
  ATM: "ATM",
  BRANCH: "Branch",
  API: "API",
};

export const fraudTypeLabels: Record<FraudType, string> = {
  SMURFING: "Smurfing / Structuring",
  SIM_SWAP: "SIM Swap Takeover",
  ACCOUNT_TAKEOVER: "Account Takeover",
  MONEY_MULE: "Money Mule Activity",
  FRAUD_RING: "Organised Fraud Ring",
  SYNTHETIC_IDENTITY: "Synthetic Identity",
  CARD_TESTING: "Card Testing",
  MERCHANT_COLLUSION: "Merchant Collusion",
  APP_FRAUD: "Authorised Push-Payment Fraud",
  INSIDER_ABUSE: "Insider Abuse",
};

export function flagFromScore(score: number): AgentFlag {
  if (score >= 0.85) return "CRITICAL";
  if (score >= 0.6) return "HIGH";
  if (score >= 0.3) return "MEDIUM";
  return "LOW";
}

export const agentFlagMeta: Record<AgentFlag, { label: string; className: string }> = {
  LOW: { label: "Low", className: "text-success" },
  MEDIUM: { label: "Medium", className: "text-warning" },
  HIGH: { label: "High", className: "text-destructive" },
  CRITICAL: { label: "Critical", className: "text-destructive" },
};

// NRB structuring bands used by the velocity agent / seeded scenarios (§8).
export const STRUCTURING_BANDS = [9_999, 49_999, 99_999];

// Disagreement variance above which the synthesis agent force-escalates to
// OTP even when the blended score alone would ALLOW (§8.2 bonus criterion).
export const DISAGREEMENT_FORCE_OTP = 0.04;

/** Maps a user's chosen transfer destination to a Track B txn_type. */
export function txnTypeForTransfer(
  destination: TransferDestination,
  bankOrWallet: string,
  mode?: "bill" | "qr" | "topup",
): TxnType {
  if (mode === "bill") return "UTILITY_BILL";
  if (mode === "topup") return "MOBILE_TOPUP";
  if (mode === "qr") return "KHALTI_QR";
  if (destination === "wallet") {
    return bankOrWallet.toLowerCase().includes("khalti") ? "KHALTI_QR" : "ESEWA_P2P";
  }
  if (destination === "other_bank") return "RTGS";
  return "ESEWA_P2P";
}

/** Best-effort mapping from an internal transaction type to a Track B txn_type. */
export function txnTypeForInternal(type: TransactionType, isWallet: boolean): TxnType {
  switch (type) {
    case "topup":
      return "MOBILE_TOPUP";
    case "qr_payment":
      return "KHALTI_QR";
    case "payment":
      return isWallet ? "ESEWA_P2P" : "UTILITY_BILL";
    case "withdrawal":
      return "ATM_WITHDRAWAL";
    case "deposit":
      return "CARD_POS";
    case "transfer":
    default:
      return isWallet ? "ESEWA_P2P" : "RTGS";
  }
}

export function formatAccountId(seq: number): string {
  return `ACC-${String(seq).padStart(7, "0")}`;
}

/**
 * Converts an internal transaction + its fraud analysis into the
 * `model_verdicts_sample.json` shape from the data manual — used by the
 * admin audit tab and the hackathon submission export.
 */
export function toModelVerdict(txn: Transaction): ModelVerdict {
  // Pre-pipeline transactions carry no analysis; fall back to the row's
  // decision/risk so exports and audit views stay total.
  const fraud = txn.fraud ?? {
    agents: [],
    synthesis: {
      finalRisk: txn.riskScore,
      confidence: 0,
      pattern: "none" as const,
      fraudType: txn.fraudType,
      decision: txn.decision,
      weights: { velocity: 0, geo: 0, behavior: 0, graph: 0 },
      disagreement: 0,
      inferenceMs: txn.latencyMs,
    },
    shap: [],
    baselineDecision: txn.decision,
    baselineCorrect: true,
  };
  const agentVerdicts = fraud.agents.map((a) => ({
    agent: a.agent as AgentName,
    score: a.risk,
    flag: a.flag,
    inference_ms: a.inferenceMs,
    reasons: a.reasons,
  }));
  agentVerdicts.push({
    agent: "synthesis",
    score: fraud.synthesis.finalRisk,
    flag: flagFromScore(fraud.synthesis.finalRisk),
    inference_ms: fraud.synthesis.inferenceMs,
    reasons: [`disagreement_score=${fraud.synthesis.disagreement.toFixed(3)}`],
  });

  return {
    txn_id: txn.id,
    account_id: formatAccountId(Number(txn.accountNumber.slice(-7)) || 1),
    txn_type: txn.txnType,
    agent_verdicts: agentVerdicts,
    weights_applied: fraud.synthesis.weights,
    fraud_probability: fraud.synthesis.finalRisk,
    fraud_decision: toTrackBDecision(
      fraud.synthesis.decision,
      fraud.synthesis.disagreement >= DISAGREEMENT_FORCE_OTP && fraud.synthesis.decision === "BLOCK",
    ),
    fraud_type_predicted: fraud.synthesis.fraudType,
    baseline_decision: toTrackBDecision(fraud.baselineDecision),
    baseline_correct: fraud.baselineCorrect,
    total_pipeline_ms: txn.latencyMs,
    disagreement_score: fraud.synthesis.disagreement,
  };
}
