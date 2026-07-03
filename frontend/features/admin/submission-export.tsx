"use client";

import * as React from "react";
import { FileDown } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { downloadSubmissionCsv } from "@/services/verdictService";
import type { Transaction } from "@/types/banking";

/**
 * Downloads a `submission_[team].csv` matching the Track B data manual's
 * export format: txn_id, fraud_probability, fraud_decision,
 * fraud_type_predicted, agent_scores_json, latency_ms.
 */
export function SubmissionExportButton({
  transactions,
  label = "Export Verdicts CSV",
}: {
  transactions: Transaction[];
  label?: string;
}) {
  const [exporting, setExporting] = React.useState(false);

  const handleExport = () => {
    setExporting(true);
    try {
      downloadSubmissionCsv(transactions, "gibl-verdicts");
      toast.success(`gibl-verdicts.csv downloaded (${transactions.length} rows)`);
    } finally {
      setExporting(false);
    }
  };

  return (
    <Button onClick={handleExport} disabled={exporting || transactions.length === 0}>
      <FileDown className="h-4 w-4" /> {label}
    </Button>
  );
}
