// Track B hackathon types are additive display/ground-truth metadata layered
// on top of this internal model — see lib/trackb.ts for the mapping layer.
import type { AgentFlag, FraudType, TxnType } from "./trackb";

export type Decision = "PASS" | "OTP" | "BLOCK";

export type TransactionStatus =
  | "success"
  | "pending"
  | "failed"
  | "blocked"
  | "otp_required";

export type TransactionType =
  | "transfer"
  | "payment"
  | "deposit"
  | "withdrawal"
  | "qr_payment"
  | "topup";

export type TransactionChannel =
  | "mobile"
  | "web"
  | "atm"
  | "branch"
  | "qr"
  | "agent";

export type TransferDestination =
  | "own"
  | "global_ime"
  | "other_bank"
  | "wallet";

export type KycStatus = "verified" | "pending" | "rejected";
export type RiskLevel = "low" | "medium" | "high";

export type FraudPattern =
  | "none"
  | "rapid_transfers"
  | "fraud_ring"
  | "money_laundering"
  | "account_takeover"
  | "novel_pattern";

export interface GeoPoint {
  city: string;
  lat: number;
  lng: number;
}

export interface Customer {
  id: string;
  name: string;
  gender: "male" | "female";
  accountNumber: string;
  mobile: string;
  email: string;
  address: string;
  city: string;
  kycStatus: KycStatus;
  riskLevel: RiskLevel;
  joinedAt: string;
  avatarColor: string;
  citizenshipNo: string;
  branch: string;

  // Track B customer_profiles alignment.
  district: string;
  province: string;
  kycTier: "TIER_1" | "TIER_2" | "TIER_3";
  isDormant: boolean;
  numBeneficiariesRegistered: number;
}

export type AccountType = "savings" | "current" | "fixed_deposit";

export interface Account {
  id: string;
  customerId: string;
  type: AccountType;
  name: string;
  accountNumber: string;
  balance: number;
  currency: "NPR";
  status: "active" | "dormant" | "frozen";
  interestRate: number;
}

export interface Card {
  id: string;
  customerId: string;
  type: "debit" | "credit";
  scheme: "visa" | "mastercard";
  number: string;
  holder: string;
  expiry: string;
  status: "active" | "blocked";
  limit: number;
}

export interface AgentResult {
  agent: "velocity" | "geo" | "behavior" | "graph";
  risk: number;
  confidence: number;
  flag: AgentFlag;
  inferenceMs: number;
  reasons: string[];
}

export interface ShapFeature {
  feature: string;
  contribution: number;
  value: string;
}

export interface FraudAnalysis {
  agents: AgentResult[];
  synthesis: {
    finalRisk: number;
    confidence: number;
    pattern: FraudPattern;
    fraudType: FraudType | null;
    decision: Decision;
    weights: { velocity: number; geo: number; behavior: number; graph: number };
    disagreement: number;
    inferenceMs: number;
  };
  shap: ShapFeature[];
  baselineDecision: Decision;
  baselineCorrect: boolean;
}

export interface Counterparty {
  name: string;
  accountNumber: string;
  bank: string;
  isWallet: boolean;
}

export interface Transaction {
  id: string;
  reference: string;
  customerId: string;
  customerName: string;
  /** Agent-space account id (ACC-*) the fraud pipeline scores on. */
  accountId?: string;
  accountNumber: string;
  counterparty: Counterparty;
  amount: number;
  direction: "debit" | "credit";
  type: TransactionType;
  channel: TransactionChannel;
  status: TransactionStatus;
  decision: Decision;
  riskScore: number;
  latencyMs: number;
  location: GeoPoint;
  device: string;
  ipAddress: string;
  remarks: string;
  timestamp: string;
  /** Full pipeline analysis; null for transactions that predate the live pipeline. */
  fraud: FraudAnalysis | null;

  // Track B hackathon fields (§4–§8 of the data manual), populated for every
  // generated / seeded / committed transaction. See lib/trackb.ts for
  // display mapping to the hackathon's own terminology.
  txnType: TxnType;
  counterpartyId: string;
  fraudType: FraudType | null;
  authMethod: "PIN" | "OTP" | "BIOMETRIC" | "PASSWORD";
  merchantCategoryCode: string;
  isVpn: boolean;
  isTor: boolean;
  impossibleTravel: boolean;
  prevTxnKm: number;
  prevTxnDeltaMin: number;
  zScoreAmount: number;
  txnCount1m: number;
  dormancyBreak: boolean;
  nightFlag: boolean;
  newCounterpartyFlag: boolean;
  deviceId: string;
}

export interface TransferRequest {
  fromAccountId: string;
  destination: TransferDestination;
  recipientAccount: string;
  recipientName: string;
  recipientBank: string;
  amount: number;
  remarks: string;
  mode?: "bill" | "qr" | "topup";
}

export interface FraudResult {
  reference: string;
  score: number;
  decision: Decision;
  pattern: FraudPattern;
  analysis: FraudAnalysis;
}

export interface AuthUser {
  customerId: string;
  name: string;
  accountNumber: string;
  mobile: string;
}

export interface SystemService {
  name: string;
  key: string;
  status: "operational" | "degraded" | "down";
  uptime: number;
  latencyMs: number;
  category: "gateway" | "agent" | "core" | "datastore";
}

export interface DashboardStats {
  todayCount: number;
  todayVolume: number;
  fraudPrevented: number;
  otpChallenges: number;
  blockedCount: number;
  activeCustomers: number;
  uptime: number;
  avgDetectionMs: number;
}

export interface TrendPoint {
  label: string;
  transactions: number;
  fraud: number;
  volume: number;
}

export interface RiskLocation {
  city: string;
  count: number;
  avgRisk: number;
}
