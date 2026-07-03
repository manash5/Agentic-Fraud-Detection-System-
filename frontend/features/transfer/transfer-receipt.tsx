"use client";

import { motion } from "framer-motion";
import { Check, Download, Home, Share2 } from "lucide-react";
import { toast } from "sonner";
import { Brand } from "@/components/shared/brand";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import { formatDateTime, formatNPR } from "@/lib/format";
import type { Transaction } from "@/types/banking";

export function TransferReceipt({
  txn,
  onDone,
}: {
  txn: Transaction;
  onDone: () => void;
}) {
  const share = () => toast.success("Receipt link copied to clipboard");
  const download = () => toast.success("Receipt downloaded as PDF");

  return (
    <div className="mx-auto max-w-md py-6">
      <motion.div
        initial={{ scale: 0.6, opacity: 0 }}
        animate={{ scale: 1, opacity: 1 }}
        transition={{ type: "spring", stiffness: 220, damping: 18 }}
        className="mx-auto mb-6 flex h-20 w-20 items-center justify-center rounded-full bg-success/12"
      >
        <motion.div
          initial={{ scale: 0 }}
          animate={{ scale: 1 }}
          transition={{ delay: 0.15, type: "spring" }}
          className="flex h-14 w-14 items-center justify-center rounded-full bg-success text-success-foreground"
        >
          <Check className="h-7 w-7" strokeWidth={3} />
        </motion.div>
      </motion.div>

      <div className="text-center">
        <h2 className="text-xl font-semibold">Transfer Successful</h2>
        <p className="mt-1 text-sm text-muted-foreground">
          Your money has been sent securely.
        </p>
        <div className="mt-4 text-3xl font-semibold tabular-nums">
          {formatNPR(txn.amount)}
        </div>
      </div>

      <div className="relative mt-8 overflow-hidden rounded-xl border bg-card">
        <div className="flex items-center justify-between border-b bg-muted/40 px-5 py-3">
          <Brand subtitle={false} />
          <span className="text-xs font-medium text-success">Completed</span>
        </div>
        <div className="space-y-3 p-5 text-sm">
          <Row label="Recipient" value={txn.counterparty.name} />
          <Row
            label="Account"
            value={txn.counterparty.accountNumber}
            mono
          />
          <Row label="Bank / Wallet" value={txn.counterparty.bank} />
          <Separator />
          <Row label="Amount" value={formatNPR(txn.amount)} strong />
          <Row label="Charge" value={formatNPR(0)} />
          <Row label="Remarks" value={txn.remarks} />
          <Separator />
          <Row label="Reference No." value={txn.reference} mono />
          <Row label="Transaction ID" value={txn.id} mono />
          <Row label="Date & Time" value={formatDateTime(txn.timestamp)} />
          <Row label="Channel" value="Mobile Banking" />
        </div>
        <div className="border-t border-dashed px-5 py-3 text-center text-[11px] text-muted-foreground">
          This is a system generated receipt. Global IME Bank Ltd.
        </div>
      </div>

      <div className="mt-6 grid grid-cols-2 gap-3">
        <Button variant="outline" onClick={share}>
          <Share2 className="h-4 w-4" /> Share
        </Button>
        <Button variant="outline" onClick={download}>
          <Download className="h-4 w-4" /> Download
        </Button>
      </div>
      <Button className="mt-3 w-full" onClick={onDone}>
        <Home className="h-4 w-4" /> Back to Home
      </Button>
    </div>
  );
}

function Row({
  label,
  value,
  mono,
  strong,
}: {
  label: string;
  value: string;
  mono?: boolean;
  strong?: boolean;
}) {
  return (
    <div className="flex items-start justify-between gap-4">
      <span className="text-muted-foreground">{label}</span>
      <span
        className={`text-right ${mono ? "font-mono text-xs" : ""} ${
          strong ? "font-semibold" : "font-medium"
        }`}
      >
        {value}
      </span>
    </div>
  );
}
