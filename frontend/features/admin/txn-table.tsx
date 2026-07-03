"use client";

import { useRouter } from "next/navigation";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Skeleton } from "@/components/ui/skeleton";
import { DecisionBadge } from "@/components/shared/decision-badge";
import { formatNPR, relativeTime } from "@/lib/format";
import { riskColor } from "@/lib/risk";
import { fraudTypeLabels, txnTypeLabels } from "@/lib/trackb";
import { cn } from "@/lib/utils";
import type { Transaction } from "@/types/banking";

export function TxnTable({
  transactions,
  loading,
  compact,
}: {
  transactions?: Transaction[];
  loading?: boolean;
  compact?: boolean;
}) {
  const router = useRouter();

  return (
    <div className="overflow-hidden rounded-xl border bg-card">
      <Table>
        <TableHeader>
          <TableRow className="hover:bg-transparent">
            <TableHead>Transaction ID</TableHead>
            <TableHead>Time</TableHead>
            {!compact && <TableHead>Customer</TableHead>}
            <TableHead className="text-right">Amount</TableHead>
            {!compact && <TableHead>txn_type</TableHead>}
            {!compact && <TableHead>fraud_type</TableHead>}
            <TableHead className="text-right">Risk</TableHead>
            <TableHead>Decision</TableHead>
            {!compact && <TableHead className="text-right">Latency</TableHead>}
          </TableRow>
        </TableHeader>
        <TableBody>
          {loading
            ? Array.from({ length: 8 }).map((_, i) => (
                <TableRow key={i}>
                  {Array.from({ length: compact ? 5 : 9 }).map((__, j) => (
                    <TableCell key={j}>
                      <Skeleton className="h-4 w-full" />
                    </TableCell>
                  ))}
                </TableRow>
              ))
            : transactions?.map((t) => (
                <TableRow
                  key={t.id}
                  className="cursor-pointer"
                  onClick={() =>
                    router.push(
                      `/admin/transactions/${encodeURIComponent(t.id)}`,
                    )
                  }
                >
                  <TableCell className="font-mono text-xs">{t.id}</TableCell>
                  <TableCell className="whitespace-nowrap text-xs text-muted-foreground">
                    {relativeTime(t.timestamp)}
                  </TableCell>
                  {!compact && (
                    <TableCell className="max-w-[160px] truncate">
                      {t.customerName}
                    </TableCell>
                  )}
                  <TableCell className="text-right font-medium tabular-nums">
                    {formatNPR(t.amount, false)}
                  </TableCell>
                  {!compact && (
                    <TableCell className="whitespace-nowrap text-xs text-muted-foreground">
                      {txnTypeLabels[t.txnType]}
                    </TableCell>
                  )}
                  {!compact && (
                    <TableCell className="whitespace-nowrap text-xs text-muted-foreground">
                      {t.fraudType ? fraudTypeLabels[t.fraudType] : "—"}
                    </TableCell>
                  )}
                  <TableCell
                    className={cn(
                      "text-right font-mono text-xs font-semibold",
                      riskColor(t.riskScore),
                    )}
                  >
                    {t.riskScore.toFixed(2)}
                  </TableCell>
                  <TableCell>
                    <DecisionBadge decision={t.decision} trackB />
                  </TableCell>
                  {!compact && (
                    <TableCell className="text-right font-mono text-xs text-muted-foreground">
                      {t.latencyMs}ms
                    </TableCell>
                  )}
                </TableRow>
              ))}
        </TableBody>
      </Table>
      {!loading && !transactions?.length && (
        <div className="py-12 text-center text-sm text-muted-foreground">
          No transactions found.
        </div>
      )}
    </div>
  );
}
