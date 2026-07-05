"use client";

import { useQuery } from "@tanstack/react-query";
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
import { getOtpEvents, type OtpEvent } from "@/services/adminService";
import { relativeTime } from "@/lib/format";

const statusMeta: Record<
  OtpEvent["status"],
  { variant: "success" | "warning" | "destructive"; label: string }
> = {
  VERIFIED: { variant: "success", label: "verified" },
  SENT: { variant: "warning", label: "pending" },
  FAILED: { variant: "destructive", label: "failed" },
  EXPIRED: { variant: "destructive", label: "expired" },
  LOCKED: { variant: "destructive", label: "locked" },
};

export default function OtpCenterPage() {
  const { data, isLoading } = useQuery({
    queryKey: ["admin", "otp-events"],
    queryFn: getOtpEvents,
    refetchInterval: 8000,
  });

  const verified = data?.filter((e) => e.status === "VERIFIED").length ?? 0;
  const pending = data?.filter((e) => e.status === "SENT").length ?? 0;
  const failed =
    data?.filter((e) => ["FAILED", "EXPIRED", "LOCKED"].includes(e.status))
      .length ?? 0;

  return (
    <div>
      <PageHeader
        title="OTP Center"
        description="SMS OTP challenges issued by the fraud pipeline (EasySendSMS)."
      />

      <div className="mb-4 grid gap-3 sm:grid-cols-3">
        <StatCard
          label="Pending Challenges"
          value={String(pending)}
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
          value={String(failed)}
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
              <TableHead>Account</TableHead>
              <TableHead>Mobile</TableHead>
              <TableHead>Trigger</TableHead>
              <TableHead>Status</TableHead>
              <TableHead className="text-right">Attempts</TableHead>
              <TableHead>Issued</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {isLoading ? (
              Array.from({ length: 8 }).map((_, i) => (
                <TableRow key={i}>
                  {Array.from({ length: 7 }).map((__, j) => (
                    <TableCell key={j}>
                      <Skeleton className="h-4 w-full" />
                    </TableCell>
                  ))}
                </TableRow>
              ))
            ) : data?.length ? (
              data.map((e) => {
                const meta = statusMeta[e.status] ?? statusMeta.SENT;
                return (
                  <TableRow key={e.id}>
                    <TableCell className="font-mono text-xs">{e.txnId}</TableCell>
                    <TableCell className="font-mono text-xs">{e.accountId}</TableCell>
                    <TableCell className="font-mono text-xs">
                      ••••••{e.mobile.slice(-4)}
                    </TableCell>
                    <TableCell className="font-mono text-[11px] text-muted-foreground">
                      {e.triggerReason ?? "—"}
                    </TableCell>
                    <TableCell>
                      <Badge variant={meta.variant}>{meta.label}</Badge>
                    </TableCell>
                    <TableCell className="text-right tabular-nums">
                      {e.attempts}
                    </TableCell>
                    <TableCell className="text-xs text-muted-foreground">
                      {relativeTime(e.sentAt)}
                    </TableCell>
                  </TableRow>
                );
              })
            ) : (
              <TableRow>
                <TableCell
                  colSpan={7}
                  className="h-24 text-center text-sm text-muted-foreground"
                >
                  No OTP challenges yet — submit a flagged transfer to see one.
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      </div>
    </div>
  );
}
