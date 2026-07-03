"use client";

import { CheckCircle2, Clock, KeyRound, XCircle } from "lucide-react";
import { PageHeader } from "@/components/admin/page-header";
import { StatCard } from "@/components/admin/stat-card";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useOtpSessions } from "@/hooks/useAdmin";
import { formatNPR, relativeTime } from "@/lib/format";

// Deterministic status from txn id so it stays stable.
function otpStatus(id: string): "verified" | "expired" | "pending" {
  const n = id.charCodeAt(id.length - 1) % 3;
  return n === 0 ? "verified" : n === 1 ? "pending" : "expired";
}

const statusMeta = {
  verified: { variant: "success", icon: CheckCircle2 },
  expired: { variant: "destructive", icon: XCircle },
  pending: { variant: "warning", icon: Clock },
} as const;

export default function OtpCenterPage() {
  const { data, isLoading } = useOtpSessions();

  const verified =
    data?.filter((t) => otpStatus(t.id) === "verified").length ?? 0;
  const expired =
    data?.filter((t) => otpStatus(t.id) === "expired").length ?? 0;

  return (
    <div>
      <PageHeader
        title="OTP Center"
        description="Dual-path OTP challenges issued by the decision service."
      />

      <div className="mb-4 grid gap-3 sm:grid-cols-3">
        <StatCard
          label="Active Challenges"
          value={String(data?.length ?? 0)}
          icon={KeyRound}
          tone="warning"
          loading={isLoading}
        />
        <StatCard
          label="Verified"
          value={String(verified)}
          icon={CheckCircle2}
          tone="success"
          loading={isLoading}
        />
        <StatCard
          label="Expired / Failed"
          value={String(expired)}
          icon={XCircle}
          tone="destructive"
          loading={isLoading}
        />
      </div>

      <div className="overflow-hidden rounded-xl border bg-card">
        <Table>
          <TableHeader>
            <TableRow className="hover:bg-transparent">
              <TableHead>Transaction</TableHead>
              <TableHead>Customer</TableHead>
              <TableHead className="text-right">Amount</TableHead>
              <TableHead>SMS</TableHead>
              <TableHead>Email</TableHead>
              <TableHead>Issued</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {isLoading
              ? Array.from({ length: 8 }).map((_, i) => (
                  <TableRow key={i}>
                    {Array.from({ length: 6 }).map((__, j) => (
                      <TableCell key={j}>
                        <Skeleton className="h-4 w-full" />
                      </TableCell>
                    ))}
                  </TableRow>
                ))
              : data?.map((t) => {
                  const status = otpStatus(t.id);
                  const meta = statusMeta[status];
                  const emailStatus =
                    status === "verified" ? "verified" : status;
                  return (
                    <TableRow key={t.id}>
                      <TableCell className="font-mono text-xs">{t.id}</TableCell>
                      <TableCell className="text-sm">{t.customerName}</TableCell>
                      <TableCell className="text-right font-medium tabular-nums">
                        {formatNPR(t.amount, false)}
                      </TableCell>
                      <TableCell>
                        <Badge variant={meta.variant}>{status}</Badge>
                      </TableCell>
                      <TableCell>
                        <Badge variant={statusMeta[emailStatus].variant}>
                          {emailStatus}
                        </Badge>
                      </TableCell>
                      <TableCell className="text-xs text-muted-foreground">
                        {relativeTime(t.timestamp)}
                      </TableCell>
                    </TableRow>
                  );
                })}
          </TableBody>
        </Table>
      </div>
    </div>
  );
}
