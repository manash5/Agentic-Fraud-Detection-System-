"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { AnimatePresence, motion } from "framer-motion";
import { Info, Loader2, Plus } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { BrandLogo, type BrandLogoId } from "@/components/shared/brand-logo";
import { AmountChips } from "./amount-chips";
import { FlowHeader } from "./flow-header";
import { ReviewRow, ReviewSheet } from "./review-sheet";
import { runTransferPipeline } from "./run-transfer-pipeline";
import { TransferBlocked } from "./transfer-blocked";
import { TransferLoadingOverlay } from "./transfer-loading";
import { TransferOtp } from "./transfer-otp";
import { TransferReceipt } from "./transfer-receipt";
import { TxnAuthStep } from "./txn-auth-step";
import { useAccounts } from "@/hooks/useBanking";
import { useAuth } from "@/lib/auth";
import { formatNPR, maskAccount } from "@/lib/format";
import { txnTypeLabels } from "@/lib/trackb";
import { commitTransfer } from "@/services/transferService";
import type { FraudResult, Transaction, TransferRequest } from "@/types/banking";

type Operator = "NTC" | "Ncell";

const OPERATORS: {
  id: Operator;
  logo: BrandLogoId;
  desc: string;
}[] = [
  { id: "NTC", logo: "ntc", desc: "Nepal Telecom prepaid recharge" },
  { id: "Ncell", logo: "ncell", desc: "Ncell prepaid / data recharge" },
];

const operatorLogo = (op: Operator): BrandLogoId => {
  if (op === "Ncell") return "ncell";
  return "ntc";
};

type Phase = "operator" | "form" | "otp" | "success" | "blocked";

export function MobileTopupFlow() {
  const router = useRouter();
  const { user } = useAuth();
  const { data: accounts } = useAccounts(user?.customerId);
  const fromAccount = accounts?.find((a) => a.type === "savings");

  const [phase, setPhase] = React.useState<Phase>("operator");
  const [operator, setOperator] = React.useState<Operator>("NTC");
  const [mobile, setMobile] = React.useState(user?.mobile ?? "");
  const [amount, setAmount] = React.useState("");
  const [remarks, setRemarks] = React.useState("");
  const [reviewOpen, setReviewOpen] = React.useState(false);
  const [authOpen, setAuthOpen] = React.useState(false);
  const [submitting, setSubmitting] = React.useState(false);

  const [fraud, setFraud] = React.useState<FraudResult | null>(null);
  const [committed, setCommitted] = React.useState<Transaction | null>(null);

  const amountNum = Number(amount) || 0;
  const txnTypeLabel = txnTypeLabels.MOBILE_TOPUP;

  const pickOperator = (op: Operator) => {
    setOperator(op);
    setPhase("form");
  };

  const buildRequest = (): TransferRequest => ({
    fromAccountId: fromAccount!.id,
    destination: "wallet",
    recipientAccount: mobile,
    recipientName: `${operator} Prepaid`,
    recipientBank: operator,
    amount: amountNum,
    remarks: remarks || `${operator} mobile top-up`,
    mode: "topup",
  });

  const openReview = () => {
    if (mobile.trim().length < 10) {
      toast.error("Enter a valid 10-digit mobile number");
      return;
    }
    if (amountNum <= 0) {
      toast.error("Enter a recharge amount");
      return;
    }
    if (fromAccount && amountNum > fromAccount.balance) {
      toast.error("Insufficient balance");
      return;
    }
    setReviewOpen(true);
  };

  const handleAuthSuccess = async () => {
    setAuthOpen(false);
    setSubmitting(true);
    try {
      const outcome = await runTransferPipeline(buildRequest());
      setFraud(outcome.fraud);
      if (outcome.kind === "blocked") setPhase("blocked");
      else if (outcome.kind === "otp") setPhase("otp");
      else {
        setCommitted(outcome.txn);
        setPhase("success");
      }
    } catch {
      toast.error("Top-up failed. Please try again.");
    } finally {
      setSubmitting(false);
    }
  };

  const finalize = async (result: FraudResult) => {
    const txn = await commitTransfer(buildRequest(), result);
    setCommitted(txn);
    setPhase("success");
  };

  if (!fromAccount) {
    return (
      <div className="flex h-64 items-center justify-center">
        <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-lg">
      <AnimatePresence mode="wait">
        {phase === "operator" && (
          <motion.div key="operator" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
            <FlowHeader title="Mobile Top-up" onBack={() => router.push("/hub")} />
            <p className="mb-4 text-sm text-muted-foreground">
              Recharge prepaid mobile balance for NTC or Ncell, not the same
              as loading an e-wallet.
            </p>
            <div className="space-y-3">
              {OPERATORS.map((op) => (
                <button
                  key={op.id}
                  onClick={() => pickOperator(op.id)}
                  className="flex w-full items-center gap-4 rounded-xl border bg-card p-4 text-left transition-all hover:border-primary/30 hover:shadow-sm"
                >
                  <BrandLogo id={op.logo} size={44} className="rounded-full" />
                  <div className="flex-1">
                    <div className="text-sm font-semibold">{op.id}</div>
                    <div className="text-xs text-muted-foreground">{op.desc}</div>
                  </div>
                </button>
              ))}
            </div>
          </motion.div>
        )}

        {phase === "form" && (
          <motion.div key="form" initial={{ opacity: 0, x: 12 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0 }}>
            <FlowHeader title="Mobile Top-up" onBack={() => setPhase("operator")} />

            <div className="relative">
            <Card>
              <CardContent className="space-y-5 p-5 sm:p-6">
                <div className="flex items-center gap-3 rounded-lg bg-muted/50 p-3">
                  <BrandLogo id={operatorLogo(operator)} size={36} className="rounded-full" />
                  <div>
                    <div className="text-sm font-semibold">{operator}</div>
                    <div className="text-xs text-muted-foreground">
                      From {fromAccount.name} · {maskAccount(fromAccount.accountNumber)}
                    </div>
                  </div>
                </div>

                <div className="space-y-1.5">
                  <Label>Mobile Number</Label>
                  <Input
                    inputMode="numeric"
                    value={mobile}
                    onChange={(e) =>
                      setMobile(e.target.value.replace(/\D/g, "").slice(0, 10))
                    }
                    placeholder="98xxxxxxxx"
                    maxLength={10}
                  />
                  <p className="text-xs text-muted-foreground">
                    The number whose prepaid balance will be recharged.
                  </p>
                </div>

                <div className="space-y-1.5">
                  <div className="flex items-center gap-1.5">
                    <Label>Recharge Amount (NPR)</Label>
                    <Info className="h-3.5 w-3.5 text-muted-foreground" />
                  </div>
                  <Input
                    type="number"
                    value={amount}
                    onChange={(e) => setAmount(e.target.value)}
                    placeholder="0.00"
                    className="h-14 text-2xl font-semibold"
                  />
                  <AmountChips
                    values={[50, 100, 200, 500, 1000]}
                    onSelect={(v) => setAmount(String(v))}
                  />
                </div>

                <div className="space-y-1.5">
                  <Label>Remarks (optional)</Label>
                  <Input
                    value={remarks}
                    onChange={(e) => setRemarks(e.target.value)}
                    placeholder="e.g. Monthly recharge"
                  />
                </div>

                <Button className="hidden w-full lg:flex" size="lg" onClick={openReview}>
                  Continue
                </Button>
              </CardContent>
            </Card>
              {submitting && <TransferLoadingOverlay />}
            </div>

            <button
              onClick={openReview}
              className="fixed bottom-24 right-6 z-30 flex h-14 w-14 items-center justify-center rounded-full bg-primary text-primary-foreground shadow-lg lg:hidden"
            >
              <Plus className="h-6 w-6" />
            </button>
          </motion.div>
        )}

        {phase === "otp" && fraud && (
          <motion.div key="otp" initial={{ opacity: 0 }} animate={{ opacity: 1 }}>
            <TransferOtp
              amount={amountNum}
              recipient={`${operator} · ${mobile}`}
              onVerified={() => finalize(fraud)}
              onCancel={() => router.push("/dashboard")}
            />
          </motion.div>
        )}

        {phase === "success" && committed && (
          <motion.div key="success" initial={{ opacity: 0 }} animate={{ opacity: 1 }}>
            <TransferReceipt txn={committed} onDone={() => router.push("/dashboard")} />
          </motion.div>
        )}

        {phase === "blocked" && fraud && (
          <motion.div key="blocked" initial={{ opacity: 0 }} animate={{ opacity: 1 }}>
            <TransferBlocked reference={fraud.reference} onDone={() => router.push("/dashboard")} />
          </motion.div>
        )}
      </AnimatePresence>

      <ReviewSheet
        open={reviewOpen}
        onOpenChange={setReviewOpen}
        confirmLabel="Confirm & Recharge"
        onConfirm={() => {
          setReviewOpen(false);
          setAuthOpen(true);
        }}
      >
        <ReviewRow label="From" value={`${fromAccount.name} · ${maskAccount(fromAccount.accountNumber)}`} />
        <ReviewRow label="Operator" value={operator} />
        <ReviewRow label="Mobile Number" value={mobile} mono />
        <ReviewRow label="Transaction Type" value={txnTypeLabel} mono />
        <ReviewRow label="Remarks" value={remarks || "—"} />
        <ReviewRow label="Amount" value={formatNPR(amountNum)} strong />
      </ReviewSheet>

      <TxnAuthStep
        open={authOpen}
        amountLabel={`Recharging ${formatNPR(amountNum)} for ${mobile}`}
        onSuccess={handleAuthSuccess}
        onCancel={() => setAuthOpen(false)}
      />
    </div>
  );
}
