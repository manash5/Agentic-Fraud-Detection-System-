import type {
  Account,
  AgentResult,
  Card,
  Customer,
  Decision,
  FraudAnalysis,
  FraudPattern,
  ShapFeature,
  Transaction,
  TransactionChannel,
  TransactionStatus,
  TransactionType,
} from "@/types/banking";
import type { FraudType, TxnType } from "@/types/trackb";
import { flagFromScore, formatAccountId, txnTypeForInternal } from "@/lib/trackb";
import {
  AVATAR_COLORS,
  BANKS,
  BRANCHES,
  CITIES,
  CITY_DISTRICTS,
  DEVICES,
  FEMALE_FIRST_NAMES,
  FOREIGN_CITIES,
  LAST_NAMES,
  MALE_FIRST_NAMES,
  MERCHANTS,
  REMARKS,
  WALLETS,
} from "./constants";
import { buildTrackBFixtures, FRAUD_MERCHANT_IDS } from "./trackb-fixtures";

// Deterministic PRNG so mock data is stable across renders/builds.
function mulberry32(seed: number) {
  return function () {
    seed |= 0;
    seed = (seed + 0x6d2b79f5) | 0;
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const rand = mulberry32(20260703);

const pick = <T>(arr: T[]): T => arr[Math.floor(rand() * arr.length)];
const int = (min: number, max: number) =>
  Math.floor(rand() * (max - min + 1)) + min;
const round2 = (n: number) => Math.round(n * 100) / 100;
const clamp = (n: number, lo = 0, hi = 1) => Math.min(hi, Math.max(lo, n));

function accountNumber(): string {
  return `${int(1, 9)}${int(0, 9)}010100${int(100000, 999999)}`;
}

function mobile(): string {
  return `98${int(0, 9)}${int(1000000, 9999999)}`;
}

function refNo(): string {
  const y = 2026;
  return `GIME${y}${int(100000, 999999)}${String.fromCharCode(
    65 + int(0, 25),
  )}${String.fromCharCode(65 + int(0, 25))}`;
}

function txnId(): string {
  const hex = "0123456789ABCDEF";
  let s = "";
  for (let i = 0; i < 8; i++) s += hex[int(0, 15)];
  return `TXN-2026${String(int(1, 12)).padStart(2, "0")}${String(
    int(1, 28),
  ).padStart(2, "0")}-${s}`;
}

// ---------------------------------------------------------------------------
// Customers, accounts, cards
// ---------------------------------------------------------------------------

function buildCustomers(count: number): Customer[] {
  const customers: Customer[] = [];
  for (let i = 0; i < count; i++) {
    const gender = rand() > 0.45 ? "male" : "female";
    const first =
      gender === "male" ? pick(MALE_FIRST_NAMES) : pick(FEMALE_FIRST_NAMES);
    const last = pick(LAST_NAMES);
    const name = `${first} ${last}`;
    const city = pick(CITIES).city;
    const geo = CITY_DISTRICTS[city] ?? { district: city, province: "Bagmati" };
    const kycRoll = rand();
    const dormant = rand() > 0.93;
    customers.push({
      id: `CUST-${String(i + 1).padStart(4, "0")}`,
      name,
      gender,
      accountNumber: accountNumber(),
      mobile: mobile(),
      email: `${first.toLowerCase()}.${last.toLowerCase()}@gmail.com`,
      address: `${pick(["Ward", "Tole", "Marg"])} ${int(1, 32)}, ${city}`,
      city,
      kycStatus:
        kycRoll > 0.85 ? "pending" : kycRoll > 0.97 ? "rejected" : "verified",
      riskLevel: rand() > 0.88 ? "high" : rand() > 0.65 ? "medium" : "low",
      joinedAt: new Date(
        Date.now() - int(60, 2200) * 86400000,
      ).toISOString(),
      avatarColor: AVATAR_COLORS[i % AVATAR_COLORS.length],
      citizenshipNo: `${int(10, 99)}-01-${int(70, 79)}-${int(10000, 99999)}`,
      branch: pick(BRANCHES),
      district: geo.district,
      province: geo.province,
      kycTier: kycRoll > 0.85 ? "TIER_1" : rand() > 0.5 ? "TIER_3" : "TIER_2",
      isDormant: dormant,
      numBeneficiariesRegistered: int(0, 9),
    });
  }
  return customers;
}

function buildAccounts(customers: Customer[]): Account[] {
  const accounts: Account[] = [];
  customers.forEach((c, idx) => {
    accounts.push({
      id: `ACC-S-${String(idx + 1).padStart(4, "0")}`,
      customerId: c.id,
      type: "savings",
      name: "Smart Savings Account",
      accountNumber: c.accountNumber,
      balance: round2(int(5000, 4500000) + rand() * 1000),
      currency: "NPR",
      status: c.isDormant ? "dormant" : "active",
      interestRate: 6.5,
    });
    if (rand() > 0.6) {
      accounts.push({
        id: `ACC-C-${String(idx + 1).padStart(4, "0")}`,
        customerId: c.id,
        type: "current",
        name: "Business Current Account",
        accountNumber: accountNumber(),
        balance: round2(int(20000, 8000000)),
        currency: "NPR",
        status: "active",
        interestRate: 0,
      });
    }
    if (rand() > 0.75) {
      accounts.push({
        id: `ACC-FD-${String(idx + 1).padStart(4, "0")}`,
        customerId: c.id,
        type: "fixed_deposit",
        name: "Fixed Deposit",
        accountNumber: accountNumber(),
        balance: round2(int(100000, 5000000)),
        currency: "NPR",
        status: "active",
        interestRate: 10.25,
      });
    }
  });
  return accounts;
}

function buildCards(customers: Customer[]): Card[] {
  const cards: Card[] = [];
  customers.forEach((c, idx) => {
    cards.push({
      id: `CARD-D-${idx + 1}`,
      customerId: c.id,
      type: "debit",
      scheme: rand() > 0.5 ? "visa" : "mastercard",
      number: `4${int(100, 999)} ${int(1000, 9999)} ${int(1000, 9999)} ${int(
        1000,
        9999,
      )}`,
      holder: c.name.toUpperCase(),
      expiry: `0${int(1, 9)}/2${int(8, 9)}`,
      status: "active",
      limit: 200000,
    });
    if (rand() > 0.7) {
      cards.push({
        id: `CARD-C-${idx + 1}`,
        customerId: c.id,
        type: "credit",
        scheme: "visa",
        number: `5${int(100, 999)} ${int(1000, 9999)} ${int(1000, 9999)} ${int(
          1000,
          9999,
        )}`,
        holder: c.name.toUpperCase(),
        expiry: `0${int(1, 9)}/2${int(8, 9)}`,
        status: rand() > 0.9 ? "blocked" : "active",
        limit: int(1, 5) * 100000,
      });
    }
  });
  return cards;
}

// ---------------------------------------------------------------------------
// Fraud analysis synthesis — 5-agent Track B pipeline
// ---------------------------------------------------------------------------

const VELOCITY_REASONS = [
  "3+ transactions within 60 seconds",
  "Amount 4.2x above account average",
  "Rapid balance drawdown detected",
  "First transfer to this beneficiary",
  "Dormant account reactivated with large amount",
  "Unusual hourly transaction rate",
];
const GEO_REASONS = [
  "Login location far from usual region",
  "Impossible travel since last session",
  "New device fingerprint",
  "Transaction near flagged fraud cluster",
  "VPN / datacenter IP detected",
  "Shared IP with 3 other accounts",
];
const BEHAVIOR_REASONS = [
  "Deviation from historical spending profile",
  "Recipient not in known network",
  "Odd-hour activity vs. baseline",
  "Sequence anomaly in recent activity",
  "Amount pattern near reporting threshold",
  "Consistent with normal behaviour",
];
const GRAPH_REASONS = [
  "Shared IP subnet with other flagged accounts",
  "Circular fund flow detected across linked accounts",
  "Within 2 hops of a known fraud-ring seed node",
  "High account out-degree in the last 24 hours",
  "Beneficiary shares a device fingerprint with sender",
  "No suspicious network relationships found",
];

const SHAP_FEATURES = [
  "amount_npr",
  "hour_of_day",
  "amount_ratio",
  "vel_z_score_amount",
  "new_beneficiary",
  "geo_distance_km",
  "device_age_days",
  "txn_count_1h",
  "is_night",
  "recipient_bank_risk",
];

const FRAUD_TYPE_POOL: FraudType[] = [
  "SMURFING",
  "SIM_SWAP",
  "ACCOUNT_TAKEOVER",
  "MONEY_MULE",
  "FRAUD_RING",
  "SYNTHETIC_IDENTITY",
  "CARD_TESTING",
  "MERCHANT_COLLUSION",
  "APP_FRAUD",
  "INSIDER_ABUSE",
];

function derivePattern(
  final: number,
  vel: number,
  geo: number,
  beh: number,
  graph: number,
): FraudPattern {
  if (final < 0.3) return "none";
  const max = Math.max(vel, geo, beh, graph);
  if (graph === max && graph > 0.55) return "fraud_ring";
  if (vel === max && vel > 0.55) return "rapid_transfers";
  if (geo === max && geo > 0.55) return "fraud_ring";
  if (Math.abs(vel - geo) < 0.1 && Math.abs(geo - beh) < 0.1)
    return "money_laundering";
  if (beh === max) return "account_takeover";
  return "novel_pattern";
}

// Per-agent weight profile by Track B txn_type — the synthesis agent leans
// on different signals depending on the payment rail (§8.2 bonus criterion:
// dynamic weighting instead of a fixed 0.4/0.3/0.3 blend).
const WEIGHT_PROFILES: Record<
  TxnType,
  { velocity: number; geo: number; behavior: number; graph: number }
> = {
  ESEWA_P2P: { velocity: 0.2, geo: 0.15, behavior: 0.25, graph: 0.4 },
  KHALTI_QR: { velocity: 0.25, geo: 0.15, behavior: 0.25, graph: 0.35 },
  CARD_POS: { velocity: 0.15, geo: 0.15, behavior: 0.4, graph: 0.3 },
  ATM_WITHDRAWAL: { velocity: 0.45, geo: 0.25, behavior: 0.2, graph: 0.1 },
  SWIFT_OUTWARD: { velocity: 0.15, geo: 0.4, behavior: 0.15, graph: 0.3 },
  RTGS: { velocity: 0.3, geo: 0.15, behavior: 0.3, graph: 0.25 },
  MOBILE_TOPUP: { velocity: 0.35, geo: 0.15, behavior: 0.35, graph: 0.15 },
  UTILITY_BILL: { velocity: 0.3, geo: 0.15, behavior: 0.4, graph: 0.15 },
};
const DEFAULT_WEIGHTS = { velocity: 0.35, geo: 0.2, behavior: 0.25, graph: 0.2 };

function buildFraud(
  baseRisk: number,
  amount: number,
  hour: number,
  foreign: boolean,
  txnType?: TxnType,
): FraudAnalysis {
  const velRisk = clamp(baseRisk + (rand() - 0.5) * 0.25);
  const geoRisk = clamp((foreign ? 0.4 : 0) + baseRisk + (rand() - 0.5) * 0.3);
  const behRisk = clamp(baseRisk + (rand() - 0.5) * 0.2);
  const graphRisk = clamp(baseRisk * 0.7 + (rand() - 0.5) * 0.3);

  const agents: AgentResult[] = [
    {
      agent: "velocity",
      risk: round2(velRisk),
      confidence: round2(clamp(0.6 + rand() * 0.35)),
      flag: flagFromScore(velRisk),
      inferenceMs: int(20, 60),
      reasons: pickReasons(VELOCITY_REASONS, velRisk),
    },
    {
      agent: "geo",
      risk: round2(geoRisk),
      confidence: round2(clamp(0.55 + rand() * 0.4)),
      flag: flagFromScore(geoRisk),
      inferenceMs: int(18, 50),
      reasons: pickReasons(GEO_REASONS, geoRisk),
    },
    {
      agent: "behavior",
      risk: round2(behRisk),
      confidence: round2(clamp(0.5 + rand() * 0.45)),
      flag: flagFromScore(behRisk),
      inferenceMs: int(25, 65),
      reasons: pickReasons(BEHAVIOR_REASONS, behRisk),
    },
    {
      agent: "graph",
      risk: round2(graphRisk),
      confidence: round2(clamp(0.55 + rand() * 0.4)),
      flag: flagFromScore(graphRisk),
      inferenceMs: int(30, 70),
      reasons: pickReasons(GRAPH_REASONS, graphRisk),
    },
  ];

  const weights = txnType ? WEIGHT_PROFILES[txnType] : DEFAULT_WEIGHTS;
  const num =
    weights.velocity * velRisk * agents[0].confidence +
    weights.geo * geoRisk * agents[1].confidence +
    weights.behavior * behRisk * agents[2].confidence +
    weights.graph * graphRisk * agents[3].confidence;
  const den =
    weights.velocity * agents[0].confidence +
    weights.geo * agents[1].confidence +
    weights.behavior * agents[2].confidence +
    weights.graph * agents[3].confidence;
  const finalRisk = round2(clamp(num / den));

  const mean = (velRisk + geoRisk + behRisk + graphRisk) / 4;
  const disagreement = round2(
    ((velRisk - mean) ** 2 +
      (geoRisk - mean) ** 2 +
      (behRisk - mean) ** 2 +
      (graphRisk - mean) ** 2) /
      4,
  );

  let decision: Decision =
    finalRisk > 0.7 ? "BLOCK" : finalRisk >= 0.3 ? "OTP" : "PASS";
  if (disagreement >= 0.04 && decision === "PASS") decision = "OTP";

  const pattern = derivePattern(finalRisk, velRisk, geoRisk, behRisk, graphRisk);
  const fraudType: FraudType | null =
    decision === "PASS" ? null : pick(FRAUD_TYPE_POOL);

  return {
    agents,
    synthesis: {
      finalRisk,
      confidence: round2(clamp(0.7 + rand() * 0.25)),
      pattern,
      fraudType: decision === "PASS" ? null : fraudType,
      decision,
      weights,
      disagreement,
      inferenceMs: agents.reduce((s, a) => s + a.inferenceMs, 0) + int(10, 25),
    },
    shap: buildShap(finalRisk, amount, hour),
    baselineDecision: decision === "BLOCK" && rand() > 0.75 ? "PASS" : decision,
    baselineCorrect: decision !== "BLOCK" || rand() <= 0.75,
  };
}

function pickReasons(pool: string[], risk: number): string[] {
  if (risk < 0.25) return [pool[pool.length - 1]];
  const n = risk > 0.65 ? 3 : 2;
  const shuffled = [...pool.slice(0, -1)].sort(() => rand() - 0.5);
  return shuffled.slice(0, n);
}

function buildShap(
  final: number,
  amount: number,
  hour: number,
): ShapFeature[] {
  const features = [...SHAP_FEATURES].sort(() => rand() - 0.5).slice(0, 6);
  return features
    .map((feature) => {
      let contribution = (rand() - 0.4) * final * 0.6;
      if (feature === "amount_npr") contribution = (amount / 1_000_000) * final;
      if (feature === "is_night" && (hour < 6 || hour > 22))
        contribution = 0.12 * final + 0.02;
      return {
        feature,
        contribution: round2(contribution),
        value: shapValue(feature, amount, hour),
      };
    })
    .sort((a, b) => Math.abs(b.contribution) - Math.abs(a.contribution));
}

function shapValue(feature: string, amount: number, hour: number): string {
  switch (feature) {
    case "amount_npr":
      return amount.toLocaleString("en-IN");
    case "hour_of_day":
      return `${hour}:00`;
    case "is_night":
      return hour < 6 || hour > 22 ? "true" : "false";
    case "geo_distance_km":
      return `${int(2, 4200)}`;
    case "device_age_days":
      return `${int(0, 900)}`;
    case "txn_count_1h":
      return `${int(1, 9)}`;
    default:
      return round2(rand()).toString();
  }
}

// ---------------------------------------------------------------------------
// Transactions
// ---------------------------------------------------------------------------

const TYPES: TransactionType[] = [
  "transfer",
  "payment",
  "qr_payment",
  "topup",
  "withdrawal",
  "deposit",
];
const CHANNELS: TransactionChannel[] = ["mobile", "web", "qr", "atm", "agent"];
const AUTH_METHODS: Transaction["authMethod"][] = [
  "PIN",
  "OTP",
  "BIOMETRIC",
  "PASSWORD",
];
const MCC_CODES = ["5411", "5812", "6011", "4900", "5999", "7995", "4814"];

function statusFromDecision(decision: Decision): TransactionStatus {
  if (decision === "BLOCK") return "blocked";
  if (decision === "OTP") return rand() > 0.35 ? "success" : "otp_required";
  return "success";
}

function buildTransactions(
  customers: Customer[],
  count: number,
): Transaction[] {
  const txns: Transaction[] = [];
  for (let i = 0; i < count; i++) {
    const customer = pick(customers);
    const type = pick(TYPES);
    const foreign = rand() > 0.9;
    const location = foreign ? pick(FOREIGN_CITIES) : pick(CITIES);
    const hour = int(0, 23);
    const daysAgo = Math.floor(rand() * rand() * 45);
    const ts = new Date(
      Date.now() -
        daysAgo * 86400000 -
        hour * 3600000 -
        int(0, 59) * 60000,
    );

    const bigAmount = rand() > 0.85;
    const amount = bigAmount
      ? round2(int(80000, 900000))
      : round2(int(150, 60000));

    let baseRisk = 0.08 + rand() * 0.18;
    if (bigAmount) baseRisk += 0.2;
    if (foreign) baseRisk += 0.2;
    if (hour < 6 || hour > 22) baseRisk += 0.1;
    if (customer.riskLevel === "high") baseRisk += 0.18;
    else if (customer.riskLevel === "medium") baseRisk += 0.08;
    baseRisk = clamp(baseRisk + (rand() - 0.5) * 0.1);

    const isWallet = type === "topup" || rand() > 0.75;
    const txnType = txnTypeForInternal(type, isWallet);
    const fraud = buildFraud(baseRisk, amount, hour, foreign, txnType);
    const isMerchantSpend = type === "payment" || type === "qr_payment";
    const flaggedMerchant = isMerchantSpend && rand() > 0.97;
    const counterpartyBank = isWallet ? pick(WALLETS) : pick(BANKS);
    const counterpartyName = isMerchantSpend
      ? pick(MERCHANTS)
      : `${pick([...MALE_FIRST_NAMES, ...FEMALE_FIRST_NAMES])} ${pick(
          LAST_NAMES,
        )}`;
    const counterpartyId = flaggedMerchant
      ? pick(FRAUD_MERCHANT_IDS)
      : isMerchantSpend
        ? `MERCH-${int(1000, 9999)}`
        : formatAccountId(int(1_000_000, 9_999_999));

    txns.push({
      id: txnId(),
      reference: refNo(),
      customerId: customer.id,
      customerName: customer.name,
      accountNumber: customer.accountNumber,
      counterparty: {
        name: counterpartyName,
        accountNumber: isWallet ? mobile() : accountNumber(),
        bank: counterpartyBank,
        isWallet,
      },
      amount,
      direction: type === "deposit" ? "credit" : "debit",
      type,
      channel: type === "withdrawal" ? "atm" : pick(CHANNELS),
      status: statusFromDecision(fraud.synthesis.decision),
      decision: fraud.synthesis.decision,
      riskScore: fraud.synthesis.finalRisk,
      latencyMs: int(180, 780),
      location,
      device: pick(DEVICES),
      ipAddress: `${int(1, 223)}.${int(0, 255)}.${int(0, 255)}.${int(1, 254)}`,
      remarks: pick(REMARKS),
      timestamp: ts.toISOString(),
      fraud,
      txnType,
      counterpartyId,
      fraudType: rand() < 0.018 ? fraud.synthesis.fraudType ?? pick(FRAUD_TYPE_POOL) : null,
      authMethod: pick(AUTH_METHODS),
      merchantCategoryCode: pick(MCC_CODES),
      isVpn: foreign && rand() > 0.7,
      isTor: foreign && rand() > 0.95,
      impossibleTravel: foreign && fraud.synthesis.finalRisk > 0.6,
      prevTxnKm: foreign ? int(500, 9000) : int(0, 40),
      prevTxnDeltaMin: int(2, 4000),
      zScoreAmount: round2(clamp((baseRisk - 0.1) * 8, -1, 4)),
      txnCount1m: int(1, 5),
      dormancyBreak: customer.isDormant && rand() > 0.6,
      nightFlag: hour < 6 || hour > 22,
      newCounterpartyFlag: rand() > 0.82,
      deviceId: `DEV-${int(10000, 99999)}`,
    });
  }
  return txns.sort(
    (a, b) =>
      new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime(),
  );
}

// ---------------------------------------------------------------------------
// Singleton in-memory database
// ---------------------------------------------------------------------------

const customers = buildCustomers(100);
const accounts = buildAccounts(customers);
const cards = buildCards(customers);
const trackBFixtures = buildTrackBFixtures(customers, accounts, {
  txnId,
  refNo,
  round2,
  int,
});
const generatedTransactions = buildTransactions(customers, 1000);
const transactions = [...trackBFixtures, ...generatedTransactions].sort(
  (a, b) => new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime(),
);

// Demo user is the first customer with fixed, memorable credentials.
const demoCustomer = customers[0];
demoCustomer.name = "Biplov Gautam";
demoCustomer.accountNumber = "1201010100456789";
demoCustomer.mobile = "9801234567";
demoCustomer.email = "biplov.gautam@gmail.com";
demoCustomer.city = "Kathmandu";
demoCustomer.branch = "Durbarmarg Branch";
demoCustomer.kycStatus = "verified";
demoCustomer.isDormant = false;
accounts
  .filter((a) => a.customerId === demoCustomer.id)
  .forEach((a, idx) => {
    if (a.type === "savings") {
      a.id = "ACC-0048293";
      a.accountNumber = demoCustomer.accountNumber;
      a.balance = 284650.75;
      a.status = "active";
    }
    if (idx === 0 && a.type !== "savings") a.balance = 1250000;
  });
cards
  .filter((c) => c.customerId === demoCustomer.id)
  .forEach((c) => (c.holder = demoCustomer.name.toUpperCase()));

export const DEMO_CREDENTIALS = {
  accountNumber: demoCustomer.accountNumber,
  mobile: demoCustomer.mobile,
  password: "Nepal@123",
  otp: "123456",
  mpin: "1234",
};

export const db = {
  customers,
  accounts,
  cards,
  transactions,
  demoCustomerId: demoCustomer.id,
};

export const mockHelpers = {
  refNo,
  txnId,
  buildFraud,
  pick,
  int,
  round2,
  clamp,
};
