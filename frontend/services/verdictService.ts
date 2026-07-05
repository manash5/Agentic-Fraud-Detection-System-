import type { Transaction } from "@/types/banking";
import type { ModelVerdict } from "@/types/trackb";
import { toModelVerdict } from "@/lib/trackb";
import { getLocalBaselineComparison } from "@/lib/txn-local-store";
import { ApiError, request } from "./http";

/** GET /verdicts/:txn_id — the synthesis_audit record in Track-B shape. */
export async function getModelVerdict(
  txnId: string,
): Promise<ModelVerdict | null> {
  try {
    return await request<ModelVerdict>(
      `/verdicts/${encodeURIComponent(txnId)}`,
    );
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) return null;
    throw error;
  }
}

export interface BaselineComparison {
  sampleSize: number;
  ruleEngineAurocPct: number;
  modelAurocPct: number;
  ruleEngineRecallPct: number;
  modelRecallPct: number;
  ruleEngineFprPct: number;
  modelFprPct: number;
  p95LatencyMs: number;
  ruleEngineWouldAllow: number;
}

/** GET /admin/baseline-comparison — model vs rule engine on labelled data. */
export async function getBaselineComparison(): Promise<BaselineComparison> {
  try {
    return await request<BaselineComparison>("/admin/baseline-comparison");
  } catch (error) {
    if (error instanceof ApiError && (error.status === 0 || error.status >= 500)) {
      return getLocalBaselineComparison();
    }
    return getLocalBaselineComparison();
  }
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

export function downloadSubmissionCsv(
  transactions: Transaction[],
  teamName = "team",
) {
  const csv = buildSubmissionCsv(transactions);
  const blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `submission_${teamName}.csv`;
  a.click();
  URL.revokeObjectURL(url);
}
