// Hand-crafted scenarios that reproduce the 7 "hidden patterns" + baseline
// comparison + COMM-042 fraud ring called out in the GIBL Hackathon 2026
// Track B manual. These are prepended into mock/db.ts's transaction feed so
// the admin fraud center and user history always have deterministic,
// judge-recognisable examples to click through — independent of the ~1,000
// randomly generated transactions.
import type {
  Account,
  AgentResult,
  Customer,
  FraudAnalysis,
  Transaction,
} from "@/types/banking";
import type { FraudType } from "@/types/trackb";
import { flagFromScore, formatAccountId } from "@/lib/trackb";
import { CITIES, DEVICES } from "./constants";

export const FRAUD_MERCHANT_IDS = ["MERCH-8812", "MERCH-9041", "MERCH-7712"];
export const COMM_042_COLLECTOR = "ACC-0011204";
export const COMM_042_MEMBERS = [
  "ACC-0033871",
  "ACC-0044129",
  "ACC-0019284",
  "ACC-0027650",
  "ACC-0038192",
  "ACC-0041007",
  "ACC-0025518",
];

interface Helpers {
  txnId: () => string;
  refNo: () => string;
  round2: (n: number) => number;
  int: (min: number, max: number) => number;
}

function agent(
  name: AgentResult["agent"],
  risk: number,
  confidence: number,
  reasons: string[],
  inferenceMs: number,
): AgentResult {
  return {
    agent: name,
    risk,
    confidence,
    flag: flagFromScore(risk),
    inferenceMs,
    reasons,
  };
}

function buildAnalysis(opts: {
  agents: [AgentResult, AgentResult, AgentResult, AgentResult];
  pattern: FraudAnalysis["synthesis"]["pattern"];
  fraudType: FraudType;
  decision: FraudAnalysis["synthesis"]["decision"];
  weights: FraudAnalysis["synthesis"]["weights"];
  disagreement: number;
  shap: FraudAnalysis["shap"];
  baselineDecision?: FraudAnalysis["synthesis"]["decision"];
  baselineCorrect?: boolean;
}): FraudAnalysis {
  const { agents, weights } = opts;
  const [velocity, geo, behavior, graph] = agents;
  const finalRisk =
    Math.round(
      (velocity.risk * weights.velocity +
        geo.risk * weights.geo +
        behavior.risk * weights.behavior +
        graph.risk * weights.graph) *
        100,
    ) / 100;
  return {
    agents,
    synthesis: {
      finalRisk,
      confidence: 0.88,
      pattern: opts.pattern,
      fraudType: opts.fraudType,
      decision: opts.decision,
      weights,
      disagreement: opts.disagreement,
      inferenceMs: agents.reduce((s, a) => s + a.inferenceMs, 0) + 22,
    },
    shap: opts.shap,
    baselineDecision: opts.baselineDecision ?? "PASS",
    baselineCorrect: opts.baselineCorrect ?? false,
  };
}

function baseTxn(
  h: Helpers,
  customer: Customer,
  account: Account,
  overrides: Partial<Transaction> & { fraud: FraudAnalysis },
): Transaction {
  return {
    id: h.txnId(),
    reference: h.refNo(),
    customerId: customer.id,
    customerName: customer.name,
    accountNumber: account.accountNumber,
    counterparty: {
      name: "Unknown",
      accountNumber: "",
      bank: "Global IME Bank",
      isWallet: false,
    },
    amount: 10000,
    direction: "debit",
    type: "transfer",
    channel: "mobile",
    status: "otp_required",
    decision: "OTP",
    riskScore: 0.5,
    latencyMs: 320,
    location: CITIES[0],
    device: DEVICES[0],
    ipAddress: "27.34.10.10",
    remarks: "Fund transfer",
    timestamp: new Date().toISOString(),
    txnType: "ESEWA_P2P",
    counterpartyId: formatAccountId(h.int(1_000_000, 9_999_999)),
    fraudType: null,
    authMethod: "OTP",
    merchantCategoryCode: "6011",
    isVpn: false,
    isTor: false,
    impossibleTravel: false,
    prevTxnKm: 4,
    prevTxnDeltaMin: 240,
    zScoreAmount: 1.1,
    txnCount1m: 1,
    dormancyBreak: false,
    nightFlag: false,
    newCounterpartyFlag: false,
    deviceId: `DEV-${h.int(10000, 99999)}`,
    ...overrides,
  };
}

function hoursAgo(h: number): string {
  return new Date(Date.now() - h * 3_600_000).toISOString();
}

/**
 * Builds ~17 seeded Track B scenarios: structuring, fraud merchants, night
 * takeover, rooted+locale-mismatch device, dormancy break, new beneficiary,
 * the COMM-042 fraud ring, and one baseline rule-engine miss.
 */
export function buildTrackBFixtures(
  customers: Customer[],
  accounts: Account[],
  h: Helpers,
): Transaction[] {
  const findAccount = (idx: number) => {
    const customer = customers[idx % customers.length];
    const account =
      accounts.find((a) => a.customerId === customer.id && a.type === "savings") ??
      accounts.find((a) => a.customerId === customer.id)!;
    return { customer, account };
  };

  const txns: Transaction[] = [];

  // 1. Structuring — three sub-threshold transfers within the hour.
  [9_999, 49_999, 99_999].forEach((amount, i) => {
    const { customer, account } = findAccount(10 + i);
    txns.push(
      baseTxn(h, customer, account, {
        amount,
        timestamp: hoursAgo(2 - i * 0.3),
        txnType: "ESEWA_P2P",
        decision: "OTP",
        status: "otp_required",
        riskScore: 0.58,
        remarks: "Family support",
        zScoreAmount: 2.6,
        txnCount1m: 3,
        counterpartyId: formatAccountId(4482210 + i),
        fraud: buildAnalysis({
          agents: [
            agent(
              "velocity",
              0.74,
              0.91,
              [
                "3 transfers within 60 minutes, each just under an NRB reporting band",
                "Amount clusters at NPR 9,999 / 49,999 / 99,999",
              ],
              38,
            ),
            agent("geo", 0.18, 0.7, ["Location consistent with home branch"], 22),
            agent("behavior", 0.32, 0.72, ["Slight deviation from spending baseline"], 41),
            agent("graph", 0.22, 0.65, ["No shared-device or ring proximity"], 29),
          ],
          pattern: "rapid_transfers",
          fraudType: "SMURFING",
          decision: "OTP",
          weights: { velocity: 0.45, geo: 0.15, behavior: 0.25, graph: 0.15 },
          disagreement: 0.045,
          shap: [
            { feature: "amount_npr", contribution: 0.31, value: amount.toLocaleString("en-IN") },
            { feature: "vel_z_score_amount", contribution: 0.24, value: "2.60" },
            { feature: "txn_count_1h", contribution: 0.19, value: "3" },
            { feature: "amount_ratio", contribution: 0.12, value: "0.97" },
          ],
          baselineDecision: "PASS",
          baselineCorrect: false,
        }),
      }),
    );
  });

  // 2. Fraud merchants — repeat card-present spend at flagged MCC merchants.
  FRAUD_MERCHANT_IDS.forEach((merchantId, i) => {
    const { customer, account } = findAccount(20 + i);
    txns.push(
      baseTxn(h, customer, account, {
        amount: 32000 + i * 4500,
        type: "payment",
        channel: "qr",
        timestamp: hoursAgo(6 + i),
        txnType: "CARD_POS",
        counterpartyId: merchantId,
        counterparty: {
          name: `Merchant ${merchantId}`,
          accountNumber: merchantId,
          bank: "Card Network",
          isWallet: false,
        },
        merchantCategoryCode: "7995",
        decision: "BLOCK",
        status: "blocked",
        riskScore: 0.83,
        fraud: buildAnalysis({
          agents: [
            agent("velocity", 0.4, 0.75, ["Amount within normal range"], 30),
            agent("geo", 0.3, 0.68, ["Consistent merchant location"], 24),
            agent(
              "behavior",
              0.71,
              0.86,
              ["Merchant category flagged for elevated chargeback rate", "First spend at this merchant"],
              44,
            ),
            agent(
              "graph",
              0.88,
              0.93,
              [
                `Merchant ${merchantId} linked to 40+ confirmed fraud cases`,
                "Shared settlement account with 2 other flagged merchants",
              ],
              52,
            ),
          ],
          pattern: "fraud_ring",
          fraudType: "MERCHANT_COLLUSION",
          decision: "BLOCK",
          weights: { velocity: 0.15, geo: 0.15, behavior: 0.25, graph: 0.45 },
          disagreement: 0.02,
          shap: [
            { feature: "recipient_bank_risk", contribution: 0.42, value: "high" },
            { feature: "mcc_risk_score", contribution: 0.33, value: "0.91" },
            { feature: "amount_npr", contribution: 0.14, value: (32000 + i * 4500).toLocaleString("en-IN") },
          ],
          baselineDecision: "PASS",
          baselineCorrect: false,
        }),
      }),
    );
  });

  // 3. Night account takeover — 01:00-04:00 NPT session from a new device.
  {
    const { customer, account } = findAccount(30);
    const ts = new Date();
    ts.setHours(2, 14, 0, 0);
    txns.push(
      baseTxn(h, customer, account, {
        amount: 145000,
        timestamp: ts.toISOString(),
        nightFlag: true,
        device: "Unrecognised Android device",
        deviceId: "DEV-77213",
        decision: "OTP",
        status: "otp_required",
        riskScore: 0.65,
        fraud: buildAnalysis({
          agents: [
            agent("velocity", 0.42, 0.7, ["First large transfer this week"], 33),
            agent("geo", 0.35, 0.66, ["Same city as registered address"], 26),
            agent(
              "behavior",
              0.79,
              0.88,
              ["Session started 02:14 local time — 4.2σ from usual activity hours", "New, unrecognised device fingerprint"],
              47,
            ),
            agent("graph", 0.2, 0.6, ["No linked fraud accounts"], 25),
          ],
          pattern: "account_takeover",
          fraudType: "ACCOUNT_TAKEOVER",
          decision: "OTP",
          weights: { velocity: 0.2, geo: 0.2, behavior: 0.45, graph: 0.15 },
          disagreement: 0.048,
          shap: [
            { feature: "is_night", contribution: 0.38, value: "true" },
            { feature: "device_age_days", contribution: 0.27, value: "0" },
            { feature: "hour_of_day", contribution: 0.16, value: "2:14" },
          ],
          baselineDecision: "PASS",
          baselineCorrect: false,
        }),
      }),
    );
  }

  // 4. Rooted device + en_US locale mismatch — SIM-swap style takeover.
  {
    const { customer, account } = findAccount(31);
    txns.push(
      baseTxn(h, customer, account, {
        amount: 480000,
        timestamp: hoursAgo(14),
        device: "Rooted Android (unknown build)",
        deviceId: "DEV-90410-ROOTED",
        isVpn: true,
        decision: "BLOCK",
        status: "blocked",
        riskScore: 0.92,
        fraud: buildAnalysis({
          agents: [
            agent("velocity", 0.55, 0.8, ["Balance drawn down 92% in single transaction"], 36),
            agent(
              "geo",
              0.86,
              0.92,
              ["Device locale en_US on a Nepal-registered account", "IP resolves to a commercial VPN exit node", "Impossible travel: 8,400km in 40 minutes"],
              48,
            ),
            agent(
              "behavior",
              0.81,
              0.9,
              ["Rooted / jailbroken device fingerprint (integrity check failed)", "SIM re-registered 6 hours before this session"],
              45,
            ),
            agent("graph", 0.4, 0.7, ["Device previously seen on 1 other account"], 30),
          ],
          pattern: "account_takeover",
          fraudType: "SIM_SWAP",
          decision: "BLOCK",
          weights: { velocity: 0.15, geo: 0.35, behavior: 0.35, graph: 0.15 },
          disagreement: 0.03,
          shap: [
            { feature: "geo_distance_km", contribution: 0.4, value: "8400" },
            { feature: "device_age_days", contribution: 0.29, value: "0" },
            { feature: "amount_ratio", contribution: 0.21, value: "0.92" },
          ],
          baselineDecision: "PASS",
          baselineCorrect: false,
        }),
        isTor: false,
      }),
    );
  }

  // 5. Dormancy break — 14-month dormant account reactivated with a large transfer.
  {
    const { customer, account } = findAccount(32);
    account.status = "dormant";
    txns.push(
      baseTxn(h, customer, account, {
        amount: 610000,
        timestamp: hoursAgo(3),
        dormancyBreak: true,
        zScoreAmount: 3.4,
        decision: "OTP",
        status: "otp_required",
        riskScore: 0.61,
        fraud: buildAnalysis({
          agents: [
            agent(
              "velocity",
              0.76,
              0.87,
              ["Account dormant for 428 days before this transaction", "Amount z-score 3.4 vs. historical average"],
              40,
            ),
            agent("geo", 0.22, 0.65, ["Consistent home location"], 24),
            agent("behavior", 0.4, 0.7, ["No prior large-value transfers on record"], 34),
            agent("graph", 0.25, 0.62, ["No ring proximity"], 27),
          ],
          pattern: "rapid_transfers",
          fraudType: "MONEY_MULE",
          decision: "OTP",
          weights: { velocity: 0.5, geo: 0.15, behavior: 0.2, graph: 0.15 },
          disagreement: 0.041,
          shap: [
            { feature: "vel_z_score_amount", contribution: 0.36, value: "3.40" },
            { feature: "dormancy_days", contribution: 0.3, value: "428" },
            { feature: "amount_npr", contribution: 0.18, value: "610,000" },
          ],
          baselineDecision: "PASS",
          baselineCorrect: false,
        }),
      }),
    );
  }

  // 6. New beneficiary added <24h before a high-value push payment.
  {
    const { customer, account } = findAccount(33);
    txns.push(
      baseTxn(h, customer, account, {
        amount: 275000,
        timestamp: hoursAgo(1),
        newCounterpartyFlag: true,
        prevTxnDeltaMin: 40,
        decision: "OTP",
        status: "otp_required",
        riskScore: 0.57,
        remarks: "Investment opportunity",
        fraud: buildAnalysis({
          agents: [
            agent(
              "velocity",
              0.62,
              0.8,
              ["Beneficiary registered 18 hours ago", "First transfer to this beneficiary exceeds NPR 200,000"],
              37,
            ),
            agent("geo", 0.2, 0.64, ["Normal login location"], 23),
            agent("behavior", 0.44, 0.73, ["Remarks pattern matches known social-engineering scripts"], 35),
            agent("graph", 0.3, 0.66, ["Beneficiary account opened 21 days ago"], 28),
          ],
          pattern: "novel_pattern",
          fraudType: "APP_FRAUD",
          decision: "OTP",
          weights: { velocity: 0.4, geo: 0.15, behavior: 0.3, graph: 0.15 },
          disagreement: 0.043,
          shap: [
            { feature: "new_beneficiary", contribution: 0.34, value: "true" },
            { feature: "prev_txn_time_delta_min", contribution: 0.22, value: "40" },
            { feature: "amount_npr", contribution: 0.19, value: "275,000" },
          ],
          baselineDecision: "PASS",
          baselineCorrect: false,
        }),
      }),
    );
  }

  // 7. COMM-042 fraud ring — 7 member accounts funnelling into a collector.
  COMM_042_MEMBERS.forEach((memberId, i) => {
    const { customer, account } = findAccount(40 + i);
    txns.push(
      baseTxn(h, customer, account, {
        amount: 38000 + i * 6100,
        timestamp: hoursAgo(20 - i * 1.5),
        counterpartyId: COMM_042_COLLECTOR,
        counterparty: {
          name: "Unregistered beneficiary",
          accountNumber: COMM_042_COLLECTOR,
          bank: "Global IME Bank",
          isWallet: false,
        },
        decision: "BLOCK",
        status: "blocked",
        riskScore: 0.89,
        ipAddress: `103.20.${140 + i}.${20 + i}`,
        fraud: buildAnalysis({
          agents: [
            agent("velocity", 0.58, 0.82, [`${i + 3} transfers to this beneficiary in 24h`], 34),
            agent("geo", 0.34, 0.7, ["Shared IP subnet with 3 other ring members"], 27),
            agent("behavior", 0.5, 0.75, ["Round-amount transfers inconsistent with stated purpose"], 36),
            agent(
              "graph",
              0.94,
              0.96,
              [
                `Direct edge into fraud-seed node ${COMM_042_COLLECTOR} (COMM-042)`,
                "Member of a 7-account circular fund-flow ring",
                "Collector account in-degree: 34 in the last 7 days",
              ],
              55,
            ),
          ],
          pattern: "fraud_ring",
          fraudType: "FRAUD_RING",
          decision: "BLOCK",
          weights: { velocity: 0.15, geo: 0.15, behavior: 0.15, graph: 0.55 },
          disagreement: 0.06,
          shap: [
            { feature: "graph_degree_in_collector", contribution: 0.46, value: "34" },
            { feature: "hop_distance_to_seed", contribution: 0.28, value: "1" },
            { feature: "amount_npr", contribution: 0.11, value: (38000 + i * 6100).toLocaleString("en-IN") },
          ],
          baselineDecision: i === 0 ? "PASS" : "BLOCK",
          baselineCorrect: i !== 0,
        }),
      }),
    );
  });

  return txns.sort(
    (a, b) => new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime(),
  );
}
