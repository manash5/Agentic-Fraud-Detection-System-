"use client";

import * as React from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { AnimatePresence, motion } from "framer-motion";
import {
  ArrowLeft,
  ArrowRight,
  Building2,
  Check,
  FileText,
  Landmark,
  Loader2,
  Users,
} from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Separator } from "@/components/ui/separator";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { BrandLogo } from "@/components/shared/brand-logo";
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
import { resolveRecipient } from "@/services/recipientService";
import { commitTransfer } from "@/services/transferService";
import { BANKS } from "@/mock/constants";
import { formatNPR, maskAccount } from "@/lib/format";
import { txnTypeForTransfer, txnTypeLabels } from "@/lib/trackb";
import { cn } from "@/lib/utils";
import type {
  Account,
  FraudResult,
  Transaction,
  TransferDestination,
  TransferRequest,
} from "@/types/banking";

type Phase = "form" | "otp" | "success" | "blocked";
type BillMode = "bill" | undefined;

const destinations: {
  id: TransferDestination;
  bill?: BillMode;
  label: string;
  desc: string;
  icon: typeof Users;
}[] = [
  { id: "own", label: "Own Account", desc: "Between your accounts", icon: Landmark },
  { id: "global_ime", label: "Global IME", desc: "To another Global IME account", icon: Users },
  { id: "other_bank", label: "Other Bank", desc: "Any bank in Nepal via RTGS", icon: Building2 },
  { id: "other_bank", bill: "bill", label: "Bill Payment", desc: "Utility & merchant bills", icon: FileText },
];

const steps = ["Destination", "Recipient", "Amount & Review"];

export function BankTransferFlow() {
  const router = useRouter();
  const params = useSearchParams();
  const { user } = useAuth();
  const { data: accounts } = useAccounts(user?.customerId);
  const fromAccount = accounts?.find((a) => a.type === "savings");

  const initialMode = params.get("mode") as BillMode | null;
  const [step, setStep] = React.useState(0);
  const [phase, setPhase] = React.useState<Phase>("form");
  const [destIndex, setDestIndex] = React.useState(
    initialMode === "bill" ? 3 : 1,
  );
  const destination = destinations[destIndex];

  const [bank, setBank] = React.useState("Global IME Bank");
  const [account, setAccount] = React.useState(params.get("account") ?? "");
  const [recipientName, setRecipientName] = React.useState(params.get("name") ?? "");
  const [resolving, setResolving] = React.useState(false);
  const [ownTargetId, setOwnTargetId] = React.useState("");
  const [amount, setAmount] = React.useState("");
  const [remarks, setRemarks] = React.useState("");
  const [reviewOpen, setReviewOpen] = React.useState(false);
  const [authOpen, setAuthOpen] = React.useState(false);
  const [submitting, setSubmitting] = React.useState(false);

  const [fraud, setFraud] = React.useState<FraudResult | null>(null);
  const [committed, setCommitted] = React.useState<Transaction | null>(null);

  const amountNum = Number(amount) || 0;
  const txnType = txnTypeForTransfer(destination.id, bank, destination.bill);
  const fee = destination.id === "other_bank" ? 10 : 0;
  const otherOwnAccounts = accounts?.filter((a) => a.id !== fromAccount?.id) ?? [];

  React.useEffect(() => {
    if (params.get("account") && params.get("name")) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setStep(1);
    }
  }, [params]);

  const pickDestination = (i: number) => {
    setDestIndex(i);
    setAccount("");
    setRecipientName("");
    setOwnTargetId("");
  };

  const doResolve = async () => {
    if (account.trim().length < 6) {
      toast.error("Enter a valid account number");
      return;
    }
    setResolving(true);
    setRecipientName("");
    try {
      const r = await resolveRecipient(account, destination.id, bank);
      setRecipientName(r.name);
      setBank(r.bank);
    } catch {
      toast.error("Could not verify this account number");
    } finally {
      setResolving(false);
    }
  };

  const pickOwnAccount = (target: Account) => {
    setOwnTargetId(target.id);
    setAccount(target.accountNumber);
    setRecipientName(user?.name ?? "");
    setBank("Global IME Bank");
  };

  const buildRequest = (): TransferRequest => ({
    fromAccountId: fromAccount!.id,
    destination: destination.id,
    recipientAccount: account,
    recipientName,
    recipientBank: bank,
    amount: amountNum,
    remarks,
    mode: destination.bill,
  });

  const openReview = () => {
    if (amountNum <= 0) {
      toast.error("Enter an amount");
      return;
    }
    if (fromAccount && amountNum > fromAccount.balance) {
      toast.error("Insufficient balance");
      return;
    }
    setReviewOpen(true);
  };

  const confirmReview = () => {
    setReviewOpen(false);
    setAuthOpen(true);
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
      toast.error("Transfer failed. Please try again.");
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
        {phase === "form" && (
          <motion.div key="form" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
            <FlowHeader title="Fund Transfer" onBack={() => router.push("/dashboard")} />

            <div className="relative">
            {/* Stepper */}
            <div className="mb-6 flex items-center gap-2">
              {steps.map((s, i) => (
                <React.Fragment key={s}>
                  <div className="flex items-center gap-2">
                    <span
                      className={cn(
                        "flex h-7 w-7 items-center justify-center rounded-full text-xs font-semibold transition-colors",
                        i < step
                          ? "bg-success text-success-foreground"
                          : i === step
                            ? "bg-primary text-primary-foreground"
                            : "bg-muted text-muted-foreground",
                      )}
                    >
                      {i < step ? <Check className="h-3.5 w-3.5" /> : i + 1}
                    </span>
                    <span
                      className={cn(
                        "hidden text-xs font-medium sm:block",
                        i === step ? "text-foreground" : "text-muted-foreground",
                      )}
                    >
                      {s}
                    </span>
                  </div>
                  {i < steps.length - 1 && <div className="h-px flex-1 bg-border" />}
                </React.Fragment>
              ))}
            </div>

            <Card>
              <CardContent className="p-5 sm:p-6">
                <div className="mb-5 flex items-center justify-between rounded-lg bg-muted/50 p-3">
                  <div className="flex items-center gap-2 text-sm">
                    <Landmark className="h-4 w-4 text-primary" />
                    <span className="text-muted-foreground">From</span>
                    <span className="font-medium">
                      {fromAccount.name} · {maskAccount(fromAccount.accountNumber)}
                    </span>
                  </div>
                  <span className="text-sm font-semibold tabular-nums">
                    {formatNPR(fromAccount.balance)}
                  </span>
                </div>

                {step === 0 && (
                  <div className="grid grid-cols-2 gap-3">
                    {destinations.map((d, i) => (
                      <button
                        key={d.label}
                        onClick={() => pickDestination(i)}
                        className={cn(
                          "flex flex-col items-start gap-2 rounded-xl border p-4 text-left transition-all",
                          destIndex === i
                            ? "border-primary bg-primary/5 ring-1 ring-primary/20"
                            : "hover:border-primary/30 hover:bg-accent",
                        )}
                      >
                        <div
                          className={cn(
                            "flex h-10 w-10 items-center justify-center overflow-hidden rounded-lg",
                            destIndex === i && d.id !== "global_ime"
                              ? "bg-primary text-primary-foreground"
                              : destIndex !== i && d.id !== "global_ime"
                                ? "bg-muted text-muted-foreground"
                                : "",
                          )}
                        >
                          {d.id === "global_ime" ? (
                            <BrandLogo id="global-ime" size={36} className="rounded-md" />
                          ) : (
                            <d.icon className="h-5 w-5" />
                          )}
                        </div>
                        <div>
                          <div className="text-sm font-semibold">{d.label}</div>
                          <div className="text-xs text-muted-foreground">{d.desc}</div>
                        </div>
                      </button>
                    ))}
                  </div>
                )}

                {step === 1 && (
                  <div className="space-y-4">
                    {destination.id === "own" ? (
                      <div className="space-y-2">
                        <Label>Transfer To</Label>
                        {otherOwnAccounts.map((a) => (
                          <button
                            key={a.id}
                            onClick={() => pickOwnAccount(a)}
                            className={cn(
                              "flex w-full items-center justify-between rounded-lg border p-3 text-left transition-colors",
                              ownTargetId === a.id
                                ? "border-primary bg-primary/5"
                                : "hover:border-primary/30 hover:bg-accent",
                            )}
                          >
                            <div>
                              <div className="text-sm font-medium">{a.name}</div>
                              <div className="font-mono text-xs text-muted-foreground">
                                {maskAccount(a.accountNumber)}
                              </div>
                            </div>
                            <span className="text-sm font-semibold tabular-nums">
                              {formatNPR(a.balance, false)}
                            </span>
                          </button>
                        ))}
                        {!otherOwnAccounts.length && (
                          <p className="text-sm text-muted-foreground">
                            You don&apos;t have other accounts to transfer between.
                          </p>
                        )}
                      </div>
                    ) : (
                      <>
                        {destination.id === "other_bank" && (
                          <div className="space-y-1.5">
                            <Label>Bank</Label>
                            <Select value={bank} onValueChange={setBank}>
                              <SelectTrigger>
                                <SelectValue />
                              </SelectTrigger>
                              <SelectContent>
                                {BANKS.map((b) => (
                                  <SelectItem key={b} value={b}>
                                    {b}
                                  </SelectItem>
                                ))}
                              </SelectContent>
                            </Select>
                          </div>
                        )}
                        <div className="space-y-1.5">
                          <Label>Account Number</Label>
                          <div className="flex gap-2">
                            <Input
                              value={account}
                              onChange={(e) => {
                                setAccount(e.target.value);
                                setRecipientName("");
                              }}
                              placeholder="Enter account number"
                              className="font-mono"
                            />
                            <Button variant="outline" onClick={doResolve} disabled={resolving}>
                              {resolving ? <Loader2 className="h-4 w-4 animate-spin" /> : "Verify"}
                            </Button>
                          </div>
                        </div>

                        <AnimatePresence>
                          {recipientName && (
                            <motion.div
                              initial={{ opacity: 0, height: 0 }}
                              animate={{ opacity: 1, height: "auto" }}
                              exit={{ opacity: 0, height: 0 }}
                              className="flex items-center gap-3 rounded-lg border border-success/30 bg-success/5 p-3"
                            >
                              <div className="flex h-9 w-9 items-center justify-center rounded-full bg-success/15 text-success">
                                <Check className="h-4 w-4" />
                              </div>
                              <div>
                                <div className="text-xs text-muted-foreground">Account verified</div>
                                <div className="text-sm font-semibold">{recipientName}</div>
                              </div>
                            </motion.div>
                          )}
                        </AnimatePresence>
                      </>
                    )}
                  </div>
                )}

                {step === 2 && (
                  <div className="space-y-4">
                    <div className="space-y-1.5">
                      <Label>Amount (NPR)</Label>
                      <Input
                        type="number"
                        value={amount}
                        onChange={(e) => setAmount(e.target.value)}
                        placeholder="0.00"
                        className="h-14 text-2xl font-semibold"
                      />
                      <AmountChips values={[1000, 5000, 25000, 100000]} onSelect={(v) => setAmount(String(v))} />
                    </div>
                    <div className="space-y-1.5">
                      <Label>Remarks / Purpose (optional)</Label>
                      <Input
                        value={remarks}
                        onChange={(e) => setRemarks(e.target.value)}
                        placeholder="e.g. Rent, family support"
                      />
                    </div>

                    <Separator />
                    <div className="space-y-2 text-sm">
                      <ReviewRow label="To" value={recipientName || "—"} />
                      <ReviewRow label="Account" value={maskAccount(account)} mono />
                      <ReviewRow label="Bank" value={bank} />
                      <ReviewRow label="Transaction Type" value={txnTypeLabels[txnType]} mono />
                      <ReviewRow label="Fee" value={formatNPR(fee)} />
                    </div>
                  </div>
                )}

                <div className="mt-6 flex gap-3">
                  {step > 0 && (
                    <Button variant="outline" onClick={() => setStep((s) => s - 1)}>
                      <ArrowLeft className="h-4 w-4" /> Back
                    </Button>
                  )}
                  {step < 2 ? (
                    <Button
                      className="flex-1"
                      disabled={
                        (step === 1 && destination.id === "own" && !ownTargetId) ||
                        (step === 1 && destination.id !== "own" && !recipientName)
                      }
                      onClick={() => setStep((s) => s + 1)}
                    >
                      Continue <ArrowRight className="h-4 w-4" />
                    </Button>
                  ) : (
                    <Button
                      className="flex-1"
                      disabled={amountNum <= 0 || amountNum > fromAccount.balance}
                      onClick={openReview}
                    >
                      {amountNum > fromAccount.balance ? "Insufficient balance" : "Review & Transfer"}
                    </Button>
                  )}
                </div>
              </CardContent>
            </Card>
              {submitting && <TransferLoadingOverlay />}
            </div>
          </motion.div>
        )}

        {phase === "otp" && fraud && (
          <motion.div key="otp" initial={{ opacity: 0 }} animate={{ opacity: 1 }}>
            <TransferOtp
              amount={amountNum}
              recipient={recipientName}
              triggerReason={otpTriggerReason(fraud)}
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
        confirmLabel="Confirm & Send"
        onConfirm={confirmReview}
      >
        <ReviewRow label="From" value={`${fromAccount.name} · ${maskAccount(fromAccount.accountNumber)}`} />
        <ReviewRow label="To" value={recipientName} />
        <ReviewRow label="Account" value={maskAccount(account)} mono />
        <ReviewRow label="Bank" value={bank} />
        <ReviewRow label="Transaction Type" value={txnTypeLabels[txnType]} mono />
        <ReviewRow label="Remarks" value={remarks || "—"} />
        <ReviewRow label="Fee" value={formatNPR(fee)} />
        <ReviewRow label="Amount" value={formatNPR(amountNum)} strong />
      </ReviewSheet>

      <TxnAuthStep
        open={authOpen}
        amountLabel={`Sending ${formatNPR(amountNum)} to ${recipientName}`}
        onSuccess={handleAuthSuccess}
        onCancel={() => setAuthOpen(false)}
      />
    </div>
  );
}

function otpTriggerReason(fraud: FraudResult): string | undefined {
  const { agents, synthesis: s } = fraud.analysis;
  if (s.disagreement >= 0.04) return "AGENT_DISAGREEMENT_FORCE_OTP";
  const top = [...agents].sort((a, b) => b.risk - a.risk)[0];
  if (!top) return undefined;
  switch (top.agent) {
    case "geo":
      return "IMPOSSIBLE_TRAVEL_DETECTED";
    case "velocity":
      return "VELOCITY_THRESHOLD_EXCEEDED";
    case "graph":
      return "FRAUD_RING_PROXIMITY";
    default:
      return "BEHAVIOURAL_ANOMALY_DETECTED";
  }
}
