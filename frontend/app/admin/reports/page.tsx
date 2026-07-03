"use client";

import {
  Download,
  FileBarChart,
  FileCheck2,
  FileClock,
  FileSpreadsheet,
  FileWarning,
} from "lucide-react";
import { toast } from "sonner";
import { PageHeader } from "@/components/admin/page-header";
import { SubmissionExportButton } from "@/features/admin/submission-export";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { useTransactions } from "@/hooks/useBanking";

const reports = [
  {
    icon: FileBarChart,
    title: "Daily Transaction Summary",
    desc: "Volume, count and channel breakdown for the day.",
    freq: "Generated daily · 00:05",
  },
  {
    icon: FileWarning,
    title: "Fraud & Risk Report",
    desc: "All flagged, OTP and blocked transactions with agent scores.",
    freq: "Generated daily",
  },
  {
    icon: FileCheck2,
    title: "AML / Suspicious Activity (STR)",
    desc: "Structuring, money-laundering and ring patterns for NRB filing.",
    freq: "Generated weekly",
  },
  {
    icon: FileSpreadsheet,
    title: "OTP Challenge Log",
    desc: "Dual-path OTP issuance, verification and expiry outcomes.",
    freq: "Generated daily",
  },
  {
    icon: FileClock,
    title: "System Performance Report",
    desc: "Agent latency, uptime and detection-time percentiles.",
    freq: "Generated weekly",
  },
  {
    icon: FileBarChart,
    title: "Customer Risk Register",
    desc: "Risk ratings and KYC status across all customers.",
    freq: "Generated monthly",
  },
];

export default function ReportsPage() {
  const { data: transactions } = useTransactions({ limit: 500 });

  return (
    <div>
      <PageHeader
        title="Reports"
        description="Download regulatory and operational reports."
      />

      <Card className="mb-4 border-primary/30 bg-primary/5">
        <CardContent className="flex flex-col items-start gap-3 p-5 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <h3 className="text-sm font-semibold">
              Transaction Verdict Export
            </h3>
            <p className="mt-1 text-xs text-muted-foreground">
              Export fraud scores, decisions, predicted types, agent scores and
              latency for all transactions in the current dataset.
            </p>
          </div>
          <SubmissionExportButton transactions={transactions ?? []} />
        </CardContent>
      </Card>

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {reports.map((r) => (
          <Card key={r.title}>
            <CardContent className="flex h-full flex-col p-5">
              <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-primary/10 text-primary">
                <r.icon className="h-5 w-5" />
              </div>
              <h3 className="mt-3 text-sm font-semibold">{r.title}</h3>
              <p className="mt-1 flex-1 text-xs text-muted-foreground">
                {r.desc}
              </p>
              <div className="mt-3 text-[11px] text-muted-foreground">
                {r.freq}
              </div>
              <div className="mt-3 flex gap-2">
                <Button
                  variant="outline"
                  size="sm"
                  className="flex-1"
                  onClick={() => toast.success(`${r.title} downloaded (PDF)`)}
                >
                  <Download className="h-3.5 w-3.5" /> PDF
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  className="flex-1"
                  onClick={() => toast.success(`${r.title} downloaded (CSV)`)}
                >
                  <Download className="h-3.5 w-3.5" /> CSV
                </Button>
              </div>
            </CardContent>
          </Card>
        ))}
      </div>
    </div>
  );
}
