"use client";

import * as React from "react";
import { motion } from "framer-motion";
import { Loader2, Mail, ShieldQuestion, Smartphone } from "lucide-react";
import { toast } from "sonner";
import { OtpInput } from "@/components/shared/otp-input";
import { Button } from "@/components/ui/button";
import { verifyTransferOtp } from "@/services/transferService";
import { formatNPR } from "@/lib/format";

export function TransferOtp({
  amount,
  recipient,
  triggerReason,
  onVerified,
  onCancel,
}: {
  amount: number;
  recipient: string;
  triggerReason?: string;
  onVerified: () => void;
  onCancel: () => void;
}) {
  const [smsOtp, setSmsOtp] = React.useState("");
  const [emailOtp, setEmailOtp] = React.useState("");
  const [loading, setLoading] = React.useState(false);
  const [attempts, setAttempts] = React.useState(0);

  const verify = async () => {
    setLoading(true);
    try {
      const { verified } = await verifyTransferOtp(smsOtp, emailOtp);
      if (verified) {
        toast.success("Both codes verified");
        onVerified();
      } else {
        const next = attempts + 1;
        setAttempts(next);
        setSmsOtp("");
        setEmailOtp("");
        toast.error("One or both codes are incorrect. Please try again.");
      }
    } finally {
      setLoading(false);
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
        <span className="font-medium text-foreground">
          {formatNPR(amount)}
        </span>{" "}
        to {recipient} needs an extra confirmation.
      </p>
      {triggerReason && (
        <p className="mx-auto mt-2 inline-block rounded-md bg-warning/10 px-2.5 py-1 font-mono text-[11px] text-warning">
          trigger_reason: {triggerReason}
        </p>
      )}
      <p className="mt-3 text-xs text-muted-foreground">
        Both the SMS and email codes must be verified before this transfer can
        proceed.
      </p>

      <div className="mt-8 space-y-6">
        <div>
          <div className="mb-2 flex items-center justify-center gap-1.5 text-xs font-medium text-muted-foreground">
            <Smartphone className="h-3.5 w-3.5" /> SMS code
          </div>
          <OtpInput value={smsOtp} onChange={setSmsOtp} autoFocus disabled={loading} />
        </div>
        <div>
          <div className="mb-2 flex items-center justify-center gap-1.5 text-xs font-medium text-muted-foreground">
            <Mail className="h-3.5 w-3.5" /> Email code
          </div>
          <OtpInput value={emailOtp} onChange={setEmailOtp} disabled={loading} />
        </div>
      </div>

      <p className="mt-4 text-xs text-muted-foreground">
        Demo codes: SMS{" "}
        <span className="font-mono font-semibold">123456</span> · Email{" "}
        <span className="font-mono font-semibold">654321</span> (any 6 digits
        accepted in each field)
      </p>

      <div className="mt-8 space-y-3">
        <Button
          className="w-full"
          size="lg"
          disabled={loading || smsOtp.length !== 6 || emailOtp.length !== 6}
          onClick={verify}
        >
          {loading ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : (
            "Verify & Complete Transfer"
          )}
        </Button>
        <Button variant="ghost" className="w-full" onClick={onCancel}>
          Cancel transfer
        </Button>
      </div>
    </div>
  );
}
