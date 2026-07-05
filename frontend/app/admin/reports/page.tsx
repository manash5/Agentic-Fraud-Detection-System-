"use client";

import * as React from "react";
import {
  Download,
  FileBarChart,
  FileSpreadsheet,
  FileWarning,
  Loader2,
} from "lucide-react";
import { toast } from "sonner";
import { PageHeader } from "@/components/admin/page-header";
import { SubmissionExportButton } from "@/features/admin/submission-export";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { useTransactions } from "@/hooks/useBanking";
import { downloadReport } from "@/services/adminService";

const reports = [
  {
    key: "daily-summary",
    icon: FileBarChart,
    title: "Daily Transaction Summary",
    desc: "Per-day volume, OTP challenges, blocks and average risk (last 14 days).",
  },
  {
    key: "flagged-transactions",
    icon: FileWarning,
    title: "Fraud & Risk Report",
    desc: "All OTP-challenged and blocked transactions with risk scores and types.",
  },
  {
    key: "otp-events",
    icon: FileSpreadsheet,
    title: "OTP Challenge Log",
    desc: "SMS OTP issuance, verification, expiry and lockout outcomes.",
  },
  {
    key: "model-verdicts",
    icon: FileBarChart,
    title: "Model Verdict Audit",
    desc: "Every synthesis decision: scores, patterns, weights and agents used.",
  },
];

export default function ReportsPage() {
  const { data: transactions } = useTransactions({ limit: 500 });
  const [downloading, setDownloading] = React.useState<string | null>(null);

  const download = async (key: string, title: string) => {
    setDownloading(key);
    try {
      await downloadReport(key);
      toast.success(`${title} downloaded`);
    } catch {
      toast.error(`Could not generate ${title}.`);
    } finally {
      setDownloading(null);
    }
  };

  return (
    <div>
      <PageHeader
        title="Reports"
        description="CSV exports generated live from the transaction ledger and audit tables."
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

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-2">
        {reports.map((r) => (
          <Card key={r.key}>
            <CardContent className="flex h-full flex-col p-5">
              <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-primary/10 text-primary">
                <r.icon className="h-5 w-5" />
              </div>
              <h3 className="mt-3 text-sm font-semibold">{r.title}</h3>
              <p className="mt-1 flex-1 text-xs text-muted-foreground">
                {r.desc}
              </p>
              <div className="mt-3">
                <Button
                  variant="outline"
                  size="sm"
                  className="w-full"
                  disabled={downloading === r.key}
                  onClick={() => download(r.key, r.title)}
                >
                  {downloading === r.key ? (
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  ) : (
                    <Download className="h-3.5 w-3.5" />
                  )}{" "}
                  Download CSV
                </Button>
              </div>
            </CardContent>
          </Card>
        ))}
      </div>
    </div>
  );
}
