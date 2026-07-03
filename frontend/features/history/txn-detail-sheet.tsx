"use client";

import { Download, Share2 } from "lucide-react";
import { toast } from "sonner";
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import { StatusBadge } from "@/components/shared/decision-badge";
import { formatDateTime, formatNPR } from "@/lib/format";
import { typeLabels } from "@/lib/risk";
import { fraudTypeLabels } from "@/lib/trackb";
import { cn } from "@/lib/utils";
import type { Transaction } from "@/types/banking";

export function TxnDetailSheet({
  txn,
  open,
  onOpenChange,
}: {
  txn: Transaction | null;
  open: boolean;
  onOpenChange: (o: boolean) => void;
}) {
  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="w-full overflow-y-auto sm:max-w-md">
        <SheetHeader>
          <SheetTitle>Transaction Details</SheetTitle>
        </SheetHeader>
        {txn && (
          <div className="space-y-5 p-6 pt-0">
            <div className="text-center">
              <div
                className={cn(
                  "text-3xl font-semibold tabular-nums",
                  txn.direction === "credit" ? "text-success" : "text-foreground",
                )}
              >
                {txn.direction === "credit" ? "+" : "-"}
                {formatNPR(txn.amount, false)}
              </div>
              <div className="mt-2 flex justify-center">
                <StatusBadge status={txn.status} />
              </div>
            </div>

            <div className="rounded-xl border">
              <Row label="Recipient" value={txn.counterparty.name} />
              <Row label="Account" value={txn.counterparty.accountNumber} mono />
              <Row label="Counterparty ID" value={txn.counterpartyId} mono />
              <Row label="Bank / Wallet" value={txn.counterparty.bank} />
              <Row label="Type" value={typeLabels[txn.type] ?? txn.type} />
              <Row label="Channel" value={txn.channel.toUpperCase()} />
              <Row label="Remarks" value={txn.remarks} />
            </div>

            <div className="rounded-xl border">
              <Row label="Reference No." value={txn.reference} mono />
              <Row label="Transaction ID" value={txn.id} mono />
              <Row label="Date & Time" value={formatDateTime(txn.timestamp)} />
              <Row label="Location" value={txn.location.city} />
            </div>

            {txn.fraudType && (
              <div className="rounded-xl border border-warning/30 bg-warning/5">
                <Row label="Flagged Pattern" value={fraudTypeLabels[txn.fraudType]} />
              </div>
            )}

            <Separator />
            <div className="grid grid-cols-2 gap-3">
              <Button
                variant="outline"
                onClick={() => toast.success("Receipt link copied")}
              >
                <Share2 className="h-4 w-4" /> Share
              </Button>
              <Button
                variant="outline"
                onClick={() => toast.success("Receipt downloaded")}
              >
                <Download className="h-4 w-4" /> Receipt
              </Button>
            </div>
          </div>
        )}
      </SheetContent>
    </Sheet>
  );
}

function Row({
  label,
  value,
  mono,
}: {
  label: string;
  value: string;
  mono?: boolean;
}) {
  return (
    <div className="flex items-start justify-between gap-4 border-b px-4 py-3 text-sm last:border-0">
      <span className="text-muted-foreground">{label}</span>
      <span className={cn("text-right font-medium", mono && "font-mono text-xs")}>
        {value}
      </span>
    </div>
  );
}
