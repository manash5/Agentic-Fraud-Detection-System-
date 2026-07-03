"use client";

import { PageHeader } from "@/components/admin/page-header";
import {
  DecisionDonut,
  FraudTrendChart,
  TransactionTrendChart,
  VolumeBarChart,
} from "@/components/admin/charts";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  useFlaggedTransactions,
  useLiveTransactions,
  useRiskLocations,
  useTrends,
} from "@/hooks/useAdmin";
import { riskColor } from "@/lib/risk";

export default function AnalyticsPage() {
  const { data: trends } = useTrends();
  const { data: locations } = useRiskLocations();
  const { data: live } = useLiveTransactions(40);
  const { data: flagged } = useFlaggedTransactions();

  const decisionCounts = live?.reduce(
    (acc, t) => {
      acc[t.decision] = (acc[t.decision] ?? 0) + 1;
      return acc;
    },
    { PASS: 0, OTP: 0, BLOCK: 0 } as Record<string, number>,
  );

  return (
    <div>
      <PageHeader
        title="Analytics"
        description="Trends and distributions across the payment ecosystem."
      />

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardContent className="p-5">
            <h3 className="mb-4 text-sm font-semibold">Transaction Trend</h3>
            {trends ? (
              <TransactionTrendChart data={trends} />
            ) : (
              <Skeleton className="h-64 w-full" />
            )}
          </CardContent>
        </Card>
        <Card>
          <CardContent className="p-5">
            <h3 className="mb-4 text-sm font-semibold">Fraud Trend</h3>
            {trends ? (
              <FraudTrendChart data={trends} />
            ) : (
              <Skeleton className="h-64 w-full" />
            )}
          </CardContent>
        </Card>
        <Card>
          <CardContent className="p-5">
            <h3 className="mb-4 text-sm font-semibold">Daily Volume (NPR)</h3>
            {trends ? (
              <VolumeBarChart data={trends} />
            ) : (
              <Skeleton className="h-56 w-full" />
            )}
          </CardContent>
        </Card>
        <Card>
          <CardContent className="p-5">
            <h3 className="mb-4 text-sm font-semibold">Decision Distribution</h3>
            {live ? (
              <DecisionDonut
                pass={decisionCounts?.PASS ?? 0}
                otp={decisionCounts?.OTP ?? 0}
                block={decisionCounts?.BLOCK ?? 0}
              />
            ) : (
              <Skeleton className="h-36 w-full" />
            )}
            <p className="mt-4 text-xs text-muted-foreground">
              {flagged?.length ?? 0} transactions currently flagged for review.
            </p>
          </CardContent>
        </Card>
      </div>

      <Card className="mt-4">
        <CardContent className="p-5">
          <h3 className="mb-4 text-sm font-semibold">
            Risk Heatmap by Location
          </h3>
          <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
            {locations?.map((loc) => (
              <div
                key={loc.city}
                className="rounded-lg border p-3"
                style={{
                  background: `color-mix(in oklch, var(--destructive) ${Math.round(
                    loc.avgRisk * 40,
                  )}%, var(--card))`,
                }}
              >
                <div className="text-sm font-medium">{loc.city}</div>
                <div className="mt-1 flex items-center justify-between">
                  <span className="text-xs text-muted-foreground">
                    {loc.count} txns
                  </span>
                  <span
                    className={`font-mono text-sm font-semibold ${riskColor(loc.avgRisk)}`}
                  >
                    {loc.avgRisk.toFixed(2)}
                  </span>
                </div>
              </div>
            )) ??
              Array.from({ length: 8 }).map((_, i) => (
                <Skeleton key={i} className="h-20" />
              ))}
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
