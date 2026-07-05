"use client";

// SMS OTP challenge issued by the backend after the fraud pipeline returned
// an OTP decision. The code was generated server-side and delivered via
// EasySendSMS; verification and completion happen entirely in the backend.
import * as React from "react";
import { motion } from "framer-motion";
import { Loader2, ShieldQuestion, Smartphone } from "lucide-react";
import { toast } from "sonner";
import { OtpInput } from "@/components/shared/otp-input";
import { Button } from "@/components/ui/button";
import {
  resendTransferOtp,
  verifyTransferOtp,
  type OtpInfo,
} from "@/services/transferService";
import { ApiError } from "@/services/http";
import { formatNPR } from "@/lib/format";
import type { Transaction } from "@/types/banking";

function useCountdown(expiresAt: string | undefined): number {
  const [left, setLeft] = React.useState(0);
  React.useEffect(() => {
    if (!expiresAt) return;
    const compute = () =>
      Math.max(0, Math.floor((new Date(expiresAt).getTime() - Date.now()) / 1000));
    setLeft(compute());
    const timer = setInterval(() => setLeft(compute()), 1000);
    return () => clearInterval(timer);
  }, [expiresAt]);
  return left;
}

export function TransferOtp({
  txnId,
  amount,
  recipient,
  otp,
  triggerReason,
  onVerified,
  onCancel,
}: {
  txnId: string;
  amount: number;
  recipient: string;
  otp: OtpInfo | null;
  triggerReason?: string;
  onVerified: (txn: Transaction) => void;
  onCancel: () => void;
}) {
  const [code, setCode] = React.useState("");
  const [loading, setLoading] = React.useState(false);
  const [resending, setResending] = React.useState(false);
  const [otpInfo, setOtpInfo] = React.useState<OtpInfo | null>(otp);
  React.useEffect(() => {
    if (otp) setOtpInfo(otp);
  }, [otp]);

  const secondsLeft = useCountdown(otpInfo?.expiresAt);
  const expired = otpInfo != null && secondsLeft <= 0;

  const verify = async () => {
    setLoading(true);
    try {
      const { txn } = await verifyTransferOtp(txnId, code);
      toast.success("Code verified — transfer completed");
      onVerified(txn);
    } catch (err) {
      setCode("");
      if (err instanceof ApiError) {
        toast.error(err.message);
        if (err.status === 429 || err.status === 410) onCancel();
      } else {
        toast.error("Verification failed. Please try again.");
      }
    } finally {
      setLoading(false);
    }
  };

  const resend = async () => {
    setResending(true);
    try {
      const { otp: fresh } = await resendTransferOtp(txnId);
      setOtpInfo(fresh);
      setCode("");
      toast.success("A new code has been sent");
    } catch (err) {
      toast.error(
        err instanceof ApiError ? err.message : "Could not resend the code.",
      );
    } finally {
      setResending(false);
    }
  };

  return (
    <div className="mx-auto max-w-md py-8 text-center">
      <motion.div
        initial={{ scale: 0.7, opacity: 0 }}
        animate={{ scale: 1, opacity: 1 }}
        className="mx-auto mb-6 flex h-20 w-20 items-center justify-center rounded-full bg-warning/15"
      >
        <ShieldQuestion className="h-9 w-9 text-warning" />
      </motion.div>

      <h2 className="text-xl font-semibold">Security Verification Required</h2>
      <p className="mx-auto mt-2 max-w-sm text-sm text-muted-foreground">
        This transfer of{" "}
        <span className="font-medium text-foreground">{formatNPR(amount)}</span>{" "}
        to {recipient} needs an extra confirmation.
      </p>
      {triggerReason && (
        <p className="mx-auto mt-2 inline-block rounded-md bg-warning/10 px-2.5 py-1 font-mono text-[11px] text-warning">
          trigger_reason: {triggerReason}
        </p>
      )}

      <div className="mt-8">
        <div className="mb-2 flex items-center justify-center gap-1.5 text-xs font-medium text-muted-foreground">
          <Smartphone className="h-3.5 w-3.5" /> Enter the 6-digit SMS code
        </div>
        <OtpInput value={code} onChange={setCode} autoFocus disabled={loading} />
      </div>

      <p className="mt-3 text-xs text-muted-foreground">
        {expired ? (
          <span className="font-medium text-destructive">
            Code expired — request a new one.
          </span>
        ) : otpInfo ? (
          <>
            Code expires in{" "}
            <span className="font-mono font-semibold text-foreground">
              {Math.floor(secondsLeft / 60)}:
              {String(secondsLeft % 60).padStart(2, "0")}
            </span>
          </>
        ) : (
          "Sending code…"
        )}
      </p>

      {otpInfo?.devCode && (
        <p className="mx-auto mt-3 max-w-xs rounded-md border border-dashed border-border bg-muted/40 px-3 py-2 text-xs text-muted-foreground">
          {otpInfo.smsFailed ? "SMS delivery failed — dev code: " : "Dev mode — code: "}
          <span className="font-mono font-semibold text-primary">
            {otpInfo.devCode}
          </span>
        </p>
      )}

      <div className="mt-8 space-y-3">
        <Button
          className="w-full"
          size="lg"
          disabled={loading || code.length !== 6 || expired}
          onClick={verify}
        >
          {loading ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : (
            "Verify & Complete Transfer"
          )}
        </Button>
        <Button
          variant="outline"
          className="w-full"
          disabled={resending}
          onClick={resend}
        >
          {resending ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : (
            "Resend code"
          )}
        </Button>
        <Button variant="ghost" className="w-full" onClick={onCancel}>
          Cancel transfer
        </Button>
      </div>
    </div>
  );
}
