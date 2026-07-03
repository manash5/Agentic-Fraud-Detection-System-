// Canonical shapes from the GIBL AI/ML Hackathon 2026 — Track B data manual.
// Kept separate from the internal banking domain (types/banking.ts) so the
// simulation UI can display exact hackathon terminology via lib/trackb.ts
// without having to rewrite the whole app's internal `Decision` model.

export type TxnType =
  | "ESEWA_P2P"
  | "CARD_POS"
  | "ATM_WITHDRAWAL"
  | "SWIFT_OUTWARD"
  | "KHALTI_QR"
  | "RTGS"
  | "MOBILE_TOPUP"
  | "UTILITY_BILL";

export type Channel = "MOBILE_APP" | "WEB" | "ATM" | "BRANCH" | "API";

export type FraudDecision = "ALLOW" | "OTP_ONLY" | "BLOCK" | "BLOCK_AND_OTP";

// §5 fraud type taxonomy.
export type FraudType =
  | "SMURFING"
  | "SIM_SWAP"
  | "ACCOUNT_TAKEOVER"
  | "MONEY_MULE"
  | "FRAUD_RING"
  | "SYNTHETIC_IDENTITY"
  | "CARD_TESTING"
  | "MERCHANT_COLLUSION"
  | "APP_FRAUD"
  | "INSIDER_ABUSE";

export type AgentName = "velocity" | "geo" | "behavior" | "graph" | "synthesis";

export type AgentFlag = "LOW" | "MEDIUM" | "HIGH" | "CRITICAL";

export interface AgentScore {
  agent: AgentName;
  score: number;
  flag: AgentFlag;
  inference_ms: number;
  reasons: string[];
}

// Mirrors model_verdicts_sample.json from the manual — used for the admin
// audit tab and the hackathon submission export.
export interface ModelVerdict {
  txn_id: string;
  account_id: string;
  txn_type: TxnType;
  agent_verdicts: AgentScore[];
  weights_applied: Record<"velocity" | "geo" | "behavior" | "graph", number>;
  fraud_probability: number;
  fraud_decision: FraudDecision;
  fraud_type_predicted: FraudType | null;
  baseline_decision: FraudDecision;
  baseline_correct: boolean;
  total_pipeline_ms: number;
  disagreement_score: number;
}
