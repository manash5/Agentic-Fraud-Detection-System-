"use client";

import { AlertTriangle, ShieldBan, ShieldQuestion } from "lucide-react";
import { PageHeader } from "@/components/admin/page-header";
import { StatCard } from "@/components/admin/stat-card";
import { TxnTable } from "@/features/admin/txn-table";
import { Card, CardContent } from "@/components/ui/card";
import { fraudTypeLabels } from "@/lib/trackb";
import { useBaselineComparison, useFlaggedTransactions } from "@/hooks/useAdmin";

export default function FraudMonitoringPage() {
  const { data, isLoading } = useFlaggedTransactions();
  const { data: baseline } = useBaselineComparison();

  const blocked = data?.filter((t) => t.decision === "BLOCK").length ?? 0;
  const otp = data?.filter((t) => t.decision === "OTP").length ?? 0;

  const fraudTypeCounts = data?.reduce(
    (acc, t) => {
      if (t.fraudType) acc[t.fraudType] = (acc[t.fraudType] ?? 0) + 1;
      return acc;
    },
    {} as Record<string, number>,
  );

  return (
    <div>
      <PageHeader
        title="Fraud Monitoring"
        description="Transactions flagged for OTP or blocked by the AI engine."
      />

      <div className="mb-4 grid gap-3 sm:grid-cols-3">
        <StatCard
          label="Flagged (OTP)"
          value={String(otp)}
          icon={ShieldQuestion}
          tone="warning"
          loading={isLoading}
        />
        <StatCard
          label="Blocked"
          value={String(blocked)}
          icon={ShieldBan}
          tone="destructive"
          loading={isLoading}
        />
        <StatCard
          label="Total Alerts"
          value={String(data?.length ?? 0)}
          icon={AlertTriangle}
          loading={isLoading}
        />
      </div>

      {baseline && (
        <Card className="mb-4 border-warning/30 bg-warning/5">
          <CardContent className="p-4 text-sm">
            <span className="font-semibold">{baseline.ruleEngineWouldAllow}</span>{" "}
            of these flagged transactions would have been{" "}
            <span className="font-mono">ALLOW</span>ed by the legacy rule
            engine (v25) — caught only by the ML fraud detection pipeline.
          </CardContent>
        </Card>
      )}

      {fraudTypeCounts && Object.keys(fraudTypeCounts).length > 0 && (
        <Card className="mb-4">
          <CardContent className="p-5">
            <h3 className="mb-3 text-sm font-semibold">fraud_type Breakdown</h3>
            <div className="flex flex-wrap gap-2">
              {Object.entries(fraudTypeCounts).map(([t, count]) => (
                <div
                  key={t}
                  className="flex items-center gap-2 rounded-lg border bg-muted/40 px-3 py-2"
                >
                  <span className="text-sm font-medium">
                    {fraudTypeLabels[t as keyof typeof fraudTypeLabels] ?? t}
                  </span>
                  <span className="rounded-md bg-primary/10 px-1.5 py-0.5 text-xs font-semibold text-primary">
                    {count}
                  </span>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      )}

      <TxnTable transactions={data} loading={isLoading} />
    </div>
  );
}
