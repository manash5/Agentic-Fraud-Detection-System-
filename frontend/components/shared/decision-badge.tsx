import { Badge } from "@/components/ui/badge";
import { decisionMeta, statusMeta } from "@/lib/risk";
import { toTrackBDecision, trackBDecisionMeta } from "@/lib/trackb";
import type { Decision, TransactionStatus } from "@/types/banking";

export function DecisionBadge({
  decision,
  trackB,
  forcedByDisagreement,
}: {
  decision: Decision;
  /** Show Track B terminology (ALLOW / OTP_ONLY / BLOCK) — used on admin surfaces. */
  trackB?: boolean;
  forcedByDisagreement?: boolean;
}) {
  if (trackB) {
    const meta = trackBDecisionMeta[toTrackBDecision(decision, forcedByDisagreement)];
    return (
      <Badge variant={meta.variant} className="gap-1.5">
        <span className={`h-1.5 w-1.5 rounded-full ${meta.dot}`} />
        {meta.label}
      </Badge>
    );
  }
  const meta = decisionMeta[decision];
  return (
    <Badge variant={meta.variant} className="gap-1.5">
      <span className={`h-1.5 w-1.5 rounded-full ${meta.dot}`} />
      {meta.label}
    </Badge>
  );
}

export function StatusBadge({ status }: { status: TransactionStatus }) {
  const meta = statusMeta[status];
  return <Badge variant={meta.variant}>{meta.label}</Badge>;
}
