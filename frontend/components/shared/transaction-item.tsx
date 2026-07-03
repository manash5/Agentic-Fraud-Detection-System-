"use client";

import {
  ArrowDownLeft,
  ArrowUpRight,
  QrCode,
  Smartphone,
  Store,
  Wallet,
} from "lucide-react";
import { formatDate, formatNPR, formatTimeShort } from "@/lib/format";
import { typeLabels } from "@/lib/risk";
import { cn } from "@/lib/utils";
import type { Transaction } from "@/types/banking";

function TxnIcon({ txn, className }: { txn: Transaction; className?: string }) {
  if (txn.direction === "credit") return <ArrowDownLeft className={className} />;
  if (txn.type === "qr_payment") return <QrCode className={className} />;
  if (txn.type === "payment") return <Store className={className} />;
  if (txn.type === "topup") return <Wallet className={className} />;
  if (txn.type === "withdrawal") return <Smartphone className={className} />;
  return <ArrowUpRight className={className} />;
}

export function TransactionItem({
  txn,
  onClick,
}: {
  txn: Transaction;
  onClick?: () => void;
}) {
  const credit = txn.direction === "credit";
  const maskedCounterparty = `##${txn.counterparty.accountNumber.slice(-4)}`;

  return (
    <button
      onClick={onClick}
      className="flex w-full items-center gap-3 rounded-lg px-2 py-2.5 text-left transition-colors hover:bg-accent"
    >
      <div
        className={cn(
          "flex h-10 w-10 shrink-0 items-center justify-center rounded-full",
          credit
            ? "bg-success/12 text-success"
            : "bg-primary/10 text-primary",
        )}
      >
        <TxnIcon txn={txn} className="h-[18px] w-[18px]" />
      </div>
      <div className="min-w-0 flex-1">
        <div className="truncate text-sm font-medium">
          {typeLabels[txn.type] ?? "Transaction"}{" "}
          <span className="text-muted-foreground">to {maskedCounterparty}</span>
        </div>
        <div className="truncate text-xs text-muted-foreground">
          {formatTimeShort(txn.timestamp)} | {formatDate(txn.timestamp)}
        </div>
      </div>
      <div className="text-right">
        <div
          className={cn(
            "text-sm font-semibold tabular-nums",
            credit ? "text-success" : "text-destructive",
          )}
        >
          {credit ? "+" : "-"}
          {formatNPR(txn.amount, false)}
        </div>
        <div className="text-[11px] text-muted-foreground">
          {txn.status === "blocked"
            ? "Blocked"
            : txn.status === "otp_required"
              ? "Pending OTP"
              : "Successful"}
        </div>
      </div>
    </button>
  );
}
