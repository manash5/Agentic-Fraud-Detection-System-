import type { Transaction } from "@/types/banking";
import type { ModelVerdict } from "@/types/trackb";
import { db } from "@/mock/db";
import { toModelVerdict } from "@/lib/trackb";
import { mockRequest } from "./http";

/** GET /verdicts/:txn_id — Track B model_verdicts_sample-shaped audit record. */
export function getModelVerdict(txnId: string): Promise<ModelVerdict | null> {
  return mockRequest(() => {
    const txn = db.transactions.find((t) => t.id === txnId);
    return txn ? toModelVerdict(txn) : null;
  });
}

export interface BaselineComparison {
  ruleEngineAurocPct: number;
  modelAurocPct: number;
  ruleEngineRecallPct: number;
  modelRecallPct: number;
  ruleEngineFprPct: number;
  modelFprPct: number;
  p95LatencyMs: number;
  ruleEngineWouldAllow: number;
}

/** GET /admin/baseline-comparison — rule engine (v25) vs. this ML system. */
export function getBaselineComparison(): Promise<BaselineComparison> {
  return mockRequest(() => {
    const flagged = db.transactions.filter((t) => t.decision !== "PASS");
    const ruleEngineWouldAllow = flagged.filter(
      (t) => t.fraud.baselineDecision === "PASS" && !t.fraud.baselineCorrect,
    ).length;
    const latencies = db.transactions.map((t) => t.latencyMs).sort((a, b) => a - b);
    const p95 = latencies[Math.floor(latencies.length * 0.95)] ?? 412;
    return {
      ruleEngineAurocPct: 71,
      modelAurocPct: 95,
      ruleEngineRecallPct: 62,
      modelRecallPct: 89,
      ruleEngineFprPct: 14,
      modelFprPct: 1.4,
      p95LatencyMs: p95,
      ruleEngineWouldAllow,
    };
  });
}

const SUBMISSION_HEADER =
  "txn_id,fraud_probability,fraud_decision,fraud_type_predicted,agent_scores_json,latency_ms";

function toCsvRow(txn: Transaction): string {
  const verdict = toModelVerdict(txn);
  const agentScores = JSON.stringify(
    Object.fromEntries(verdict.agent_verdicts.map((a) => [a.agent, a.score])),
  ).replace(/"/g, '""');
  return [
    verdict.txn_id,
    verdict.fraud_probability.toFixed(4),
    verdict.fraud_decision,
    verdict.fraud_type_predicted ?? "",
    `"${agentScores}"`,
    verdict.total_pipeline_ms,
  ].join(",");
}

/** Builds the `submission_[team].csv` export described in the data manual. */
export function buildSubmissionCsv(transactions: Transaction[]): string {
  return [SUBMISSION_HEADER, ...transactions.map(toCsvRow)].join("\n");
}

export function downloadSubmissionCsv(transactions: Transaction[], teamName = "team") {
  const csv = buildSubmissionCsv(transactions);
  const blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `submission_${teamName}.csv`;
  a.click();
  URL.revokeObjectURL(url);
}
