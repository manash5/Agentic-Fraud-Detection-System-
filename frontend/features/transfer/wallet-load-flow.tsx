"use client";

import * as React from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { AnimatePresence, motion } from "framer-motion";
import { BookUser, Info, Loader2, Plus, Star, Tag } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { BrandLogo, type BrandLogoId } from "@/components/shared/brand-logo";
import { AmountChips } from "./amount-chips";
import { FlowHeader } from "./flow-header";
import { ReviewRow, ReviewSheet } from "./review-sheet";
import { TransferBlocked } from "./transfer-blocked";
import { TransferLoadingOverlay } from "./transfer-loading";
import { TransferOtp } from "./transfer-otp";
import { TransferProcessing } from "./transfer-processing";
import { TransferReceipt } from "./transfer-receipt";
import { otpTriggerReason, useTransferPhases } from "./use-transfer-phases";
import { useTransferRun } from "./use-transfer-run";
import { useAccounts } from "@/hooks/useBanking";
import { useAuth } from "@/lib/auth";
import { formatNPR, maskAccount } from "@/lib/format";
import { txnTypeForTransfer, txnTypeLabels } from "@/lib/trackb";
import { ApiError } from "@/services/http";
import type { TransferRequest } from "@/types/banking";

type Provider = "eSewa" | "Khalti";

const PROVIDERS: { id: Provider; logo: BrandLogoId }[] = [
  { id: "eSewa", logo: "esewa" },
  { id: "Khalti", logo: "khalti" },
];

const providerLogo = (p: Provider): BrandLogoId =>
  p === "Khalti" ? "khalti" : "esewa";

type Screen = "provider" | "form";

export function WalletLoadFlow() {
  const router = useRouter();
  const params = useSearchParams();
  const { user } = useAuth();
  const { data: accounts } = useAccounts(user?.customerId);
  const fromAccount = accounts?.find((a) => a.type === "savings");

  const initialProvider = params.get("provider");

  const [screen, setScreen] = React.useState<Screen>(
    initialProvider ? "form" : "provider",
  );
  const [provider, setProvider] = React.useState<Provider>(
    (initialProvider === "khalti" ? "Khalti" : "eSewa") as Provider,
  );
  const [walletId, setWalletId] = React.useState("");
  const [amount, setAmount] = React.useState("");
  const [remarks, setRemarks] = React.useState("");
  const [reviewOpen, setReviewOpen] = React.useState(false);
  const [submitting, setSubmitting] = React.useState(false);

  const run = useTransferRun();
  const { phase: pipelinePhase, committed } = useTransferPhases(run);
  const phase = pipelinePhase === "form" ? screen : pipelinePhase;

  const amountNum = Number(amount) || 0;
  const txnType = txnTypeForTransfer("wallet", provider);

  const pickProvider = (p: Provider) => {
    setProvider(p);
    setScreen("form");
  };

  const buildRequest = (): TransferRequest => ({
    fromAccountId: fromAccount!.id,
    destination: "wallet",
    recipientAccount: walletId,
    recipientName: `${provider} Wallet`,
    recipientBank: provider,
    amount: amountNum,
    remarks: remarks || `${provider} wallet load`,
  });

  const openReview = () => {
    if (walletId.trim().length < 7) {
      toast.error("Enter a valid wallet number or email");
      return;
    }
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

  const confirmReview = async () => {
    setReviewOpen(false);
    setSubmitting(true);
    try {
      await run.start(buildRequest());
    } catch (err) {
      toast.error(
        err instanceof ApiError
          ? err.message
          : "Wallet load failed. Please try again.",
      );
    } finally {
      setSubmitting(false);
    }
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
        {phase === "provider" && (
          <motion.div key="provider" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
            <FlowHeader title="Wallet Load" onBack={() => router.push("/hub")} />
            <div className="space-y-3">
              {PROVIDERS.map((p) => (
                <button
                  key={p.id}
                  onClick={() => pickProvider(p.id)}
                  className="flex w-full items-center gap-4 rounded-xl border bg-card p-4 text-left transition-all hover:border-primary/30 hover:shadow-sm"
                >
                  <BrandLogo id={p.logo} size={44} className="rounded-full" />
                  <div className="flex-1">
                    <div className="text-sm font-semibold">{p.id}</div>
                    <div className="text-xs text-muted-foreground">
                      Load your {p.id} wallet from your Global IME account
                    </div>
                  </div>
                </button>
              ))}
            </div>
          </motion.div>
        )}

        {phase === "form" && (
          <motion.div key="form" initial={{ opacity: 0, x: 12 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0 }}>
            <FlowHeader title="Wallet Load" onBack={() => setScreen("provider")} />

            <div className="relative">
              <Card>
                <CardContent className="space-y-5 p-5 sm:p-6">
                  <div className="flex items-center gap-3 rounded-lg bg-muted/50 p-3">
                    <BrandLogo id={providerLogo(provider)} size={36} className="rounded-full" />
                  <div>
                    <div className="text-sm font-semibold">{provider}</div>
                    <div className="text-xs text-muted-foreground">
                      From {fromAccount.name} · {maskAccount(fromAccount.accountNumber)}
                    </div>
                  </div>
                </div>

                <div className="space-y-1.5">
                  <Label>Wallet Number / Email</Label>
                  <div className="flex gap-2">
                    <Input
                      value={walletId}
                      onChange={(e) => setWalletId(e.target.value)}
                      placeholder="98xxxxxxxx or email"
                    />
                    <Button
                      type="button"
                      variant="outline"
                      size="icon"
                      onClick={() => toast.info("No saved favourites yet")}
                    >
                      <Star className="h-4 w-4" />
                    </Button>
                    <Button
                      type="button"
                      variant="outline"
                      size="icon"
                      onClick={() => toast.info("Contacts access coming soon")}
                    >
                      <BookUser className="h-4 w-4" />
                    </Button>
                  </div>
                </div>

                <div className="space-y-1.5">
                  <div className="flex items-center gap-1.5">
                    <Label>Amount (NPR)</Label>
                    <Info className="h-3.5 w-3.5 text-muted-foreground" />
                  </div>
                  <Input
                    type="number"
                    value={amount}
                    onChange={(e) => setAmount(e.target.value)}
                    placeholder="0.00"
                    className="h-14 text-2xl font-semibold"
                  />
                  <AmountChips onSelect={(v) => setAmount(String(v))} />
                </div>

                <div className="space-y-1.5">
                  <Label>Remarks (optional)</Label>
                  <Input
                    value={remarks}
                    onChange={(e) => setRemarks(e.target.value)}
                    placeholder="e.g. Monthly wallet load"
                  />
                </div>

                <button
                  onClick={() => toast.info("Promo codes coming soon")}
                  className="flex items-center gap-1.5 text-xs font-medium text-primary hover:underline"
                >
                  <Tag className="h-3.5 w-3.5" /> Add Promo Code?
                </button>

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

        {phase === "processing" && (
          <motion.div key="processing" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
            <FlowHeader title="Wallet Load" onBack={() => undefined} />
            <TransferProcessing amount={amountNum} recipient={`${provider} Wallet`} slow={run.timedOut} />
          </motion.div>
        )}

        {phase === "otp" && run.txnId && (
          <motion.div key="otp" initial={{ opacity: 0 }} animate={{ opacity: 1 }}>
            <TransferOtp
              txnId={run.txnId}
              amount={amountNum}
              recipient={`${provider} Wallet`}
              otp={run.status?.otp ?? null}
              triggerReason={otpTriggerReason(run.status)}
              onVerified={run.markVerified}
              onCancel={() => router.push("/dashboard")}
            />
          </motion.div>
        )}

        {phase === "success" && committed && (
          <motion.div key="success" initial={{ opacity: 0 }} animate={{ opacity: 1 }}>
            <TransferReceipt txn={committed} onDone={() => router.push("/dashboard")} />
          </motion.div>
        )}

        {phase === "blocked" && (
          <motion.div key="blocked" initial={{ opacity: 0 }} animate={{ opacity: 1 }}>
            <TransferBlocked
              reference={run.status?.fraud?.reference ?? run.status?.txn?.reference ?? run.txnId ?? ""}
              onDone={() => router.push("/dashboard")}
            />
          </motion.div>
        )}
      </AnimatePresence>

      <ReviewSheet
        open={reviewOpen}
        onOpenChange={setReviewOpen}
        confirmLabel="Confirm & Load Wallet"
        onConfirm={confirmReview}
      >
        <ReviewRow label="From" value={`${fromAccount.name} · ${maskAccount(fromAccount.accountNumber)}`} />
        <ReviewRow label="Provider" value={provider} />
        <ReviewRow label="Wallet ID" value={walletId} mono />
        <ReviewRow label="Transaction Type" value={txnTypeLabels[txnType]} mono />
        <ReviewRow label="Remarks" value={remarks || "—"} />
        <ReviewRow label="Amount" value={formatNPR(amountNum)} strong />
      </ReviewSheet>
    </div>
  );
}
