"use client";

import {
  Activity,
  Clock,
  KeyRound,
  ShieldBan,
  ShieldCheck,
  TrendingUp,
  Users,
  Zap,
} from "lucide-react";
import { StatCard } from "@/components/admin/stat-card";
import {
  DecisionDonut,
  FraudTrendChart,
  TransactionTrendChart,
} from "@/components/admin/charts";
import { TxnTable } from "./txn-table";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  useBaselineComparison,
  useDashboardStats,
  useLiveTransactions,
  useRiskLocations,
  useTrends,
} from "@/hooks/useAdmin";
import { formatCompact } from "@/lib/format";
import { riskColor } from "@/lib/risk";

export function AdminDashboardView() {
  const { data: stats, isLoading: statsLoading } = useDashboardStats();
  const { data: trends } = useTrends();
  const { data: locations } = useRiskLocations();
  const { data: live, isLoading: liveLoading } = useLiveTransactions(12);
  const { data: baseline } = useBaselineComparison();

  const decisionCounts = live?.reduce(
    (acc, t) => {
      acc[t.decision] = (acc[t.decision] ?? 0) + 1;
      return acc;
    },
    { PASS: 0, OTP: 0, BLOCK: 0 } as Record<string, number>,
  );

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">
          Fraud Monitoring Center
        </h1>
        <p className="text-sm text-muted-foreground">
          Real-time overview of transactions and AI fraud detection.
        </p>
      </div>

      {/* Stat cards */}
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <StatCard
          label="Today's Transactions"
          value={stats ? stats.todayCount.toLocaleString() : "—"}
          delta={{ value: "8.2%", up: true }}
          icon={Activity}
          loading={statsLoading}
        />
        <StatCard
          label="Today's Volume"
          value={stats ? `NPR ${formatCompact(stats.todayVolume)}` : "—"}
          delta={{ value: "3.1%", up: true }}
          icon={TrendingUp}
          loading={statsLoading}
        />
        <StatCard
          label="Fraud Prevented"
          value={stats ? `NPR ${formatCompact(stats.fraudPrevented)}` : "—"}
          delta={{ value: "12.4%", up: true, good: true }}
          icon={ShieldCheck}
          tone="success"
          loading={statsLoading}
        />
        <StatCard
          label="Blocked Transactions"
          value={stats ? stats.blockedCount.toLocaleString() : "—"}
          delta={{ value: "2.0%", up: false, good: true }}
          icon={ShieldBan}
          tone="destructive"
          loading={statsLoading}
        />
        <StatCard
          label="OTP Challenges"
          value={stats ? stats.otpChallenges.toLocaleString() : "—"}
          icon={KeyRound}
          tone="warning"
          loading={statsLoading}
        />
        <StatCard
          label="Active Customers"
          value={stats ? stats.activeCustomers.toLocaleString() : "—"}
          icon={Users}
          loading={statsLoading}
        />
        <StatCard
          label="System Uptime"
          value={stats ? `${stats.uptime}%` : "—"}
          icon={Zap}
          tone="success"
          loading={statsLoading}
        />
        <StatCard
          label="Avg Detection Time"
          value={stats ? `${stats.avgDetectionMs}ms` : "—"}
          icon={Clock}
          loading={statsLoading}
        />
      </div>

      {/* Track B model vs. baseline */}
      {baseline && (
        <Card>
          <CardContent className="p-5">
            <div className="mb-4 flex items-center justify-between">
              <h3 className="text-sm font-semibold">
                Model Performance vs. Rule Engine (v25)
              </h3>
              <span className="rounded-md bg-muted px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
                Simulated
              </span>
            </div>
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
              <BaselineMetric
                label="AUROC"
                baseline={`${baseline.ruleEngineAurocPct}%`}
                model={`${baseline.modelAurocPct}%`}
                target=">93%"
              />
              <BaselineMetric
                label="Recall"
                baseline={`${baseline.ruleEngineRecallPct}%`}
                model={`${baseline.modelRecallPct}%`}
                target=">88%"
              />
              <BaselineMetric
                label="False Positive Rate"
                baseline={`${baseline.ruleEngineFprPct}%`}
                model={`${baseline.modelFprPct}%`}
                target="<5%"
              />
              <BaselineMetric
                label="P95 Latency"
                baseline="—"
                model={`${baseline.p95LatencyMs}ms`}
                target="<800ms"
              />
            </div>
            <p className="mt-4 text-xs text-muted-foreground">
              Top model features (§8.3): z_score_amount ·
              prev_txn_time_delta_min · prev_txn_km · device_age_days ·
              graph_degree_in_collector
            </p>
          </CardContent>
        </Card>
      )}

      {/* Charts */}
      <div className="grid gap-4 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardContent className="p-5">
            <h3 className="mb-4 text-sm font-semibold">
              Transaction Trend · 14 days
            </h3>
            {trends ? (
              <TransactionTrendChart data={trends} />
            ) : (
              <Skeleton className="h-64 w-full" />
            )}
          </CardContent>
        </Card>
        <Card>
          <CardContent className="p-5">
            <h3 className="mb-4 text-sm font-semibold">Decision Split</h3>
            {live ? (
              <DecisionDonut
                pass={decisionCounts?.PASS ?? 0}
                otp={decisionCounts?.OTP ?? 0}
                block={decisionCounts?.BLOCK ?? 0}
              />
            ) : (
              <Skeleton className="h-36 w-full" />
            )}
          </CardContent>
        </Card>
      </div>

      <div className="grid gap-4 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardContent className="p-5">
            <h3 className="mb-4 text-sm font-semibold">Fraud Trend · 14 days</h3>
            {trends ? (
              <FraudTrendChart data={trends} />
            ) : (
              <Skeleton className="h-64 w-full" />
            )}
          </CardContent>
        </Card>
        <Card>
          <CardContent className="p-5">
            <h3 className="mb-4 text-sm font-semibold">Top Risk Locations</h3>
            <div className="space-y-2.5">
              {locations?.slice(0, 7).map((loc) => (
                <div key={loc.city} className="flex items-center gap-3">
                  <span className="w-24 truncate text-sm">{loc.city}</span>
                  <div className="h-2 flex-1 overflow-hidden rounded-full bg-muted">
                    <div
                      className="h-full rounded-full bg-primary"
                      style={{ width: `${loc.avgRisk * 100}%` }}
                    />
                  </div>
                  <span
                    className={`w-10 text-right font-mono text-xs font-semibold ${riskColor(loc.avgRisk)}`}
                  >
                    {loc.avgRisk.toFixed(2)}
                  </span>
                </div>
              )) ??
                Array.from({ length: 6 }).map((_, i) => (
                  <Skeleton key={i} className="h-5 w-full" />
                ))}
            </div>
          </CardContent>
        </Card>
      </div>

      {/* Live table */}
      <div>
        <div className="mb-3 flex items-center gap-2">
          <span className="relative flex h-2 w-2">
            <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-success opacity-75" />
            <span className="relative inline-flex h-2 w-2 rounded-full bg-success" />
          </span>
          <h3 className="text-sm font-semibold">Live Transactions</h3>
        </div>
        <TxnTable transactions={live} loading={liveLoading} />
      </div>
    </div>
  );
}

function BaselineMetric({
  label,
  baseline,
  model,
  target,
}: {
  label: string;
  baseline: string;
  model: string;
  target: string;
}) {
  return (
    <div className="rounded-lg border p-3">
      <div className="text-[10px] uppercase tracking-wider text-muted-foreground">
        {label}
      </div>
      <div className="mt-1.5 flex items-baseline gap-1.5">
        <span className="text-lg font-semibold tabular-nums text-success">
          {model}
        </span>
        <span className="text-xs text-muted-foreground line-through">
          {baseline}
        </span>
      </div>
      <div className="mt-0.5 text-[10px] text-muted-foreground">
        Target {target}
      </div>
    </div>
  );
}
