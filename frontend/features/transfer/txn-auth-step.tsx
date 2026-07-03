"use client";

import * as React from "react";
import { X } from "lucide-react";
import { toast } from "sonner";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import { BiometricPrompt } from "@/features/auth/biometric-prompt";
import { MpinKeypad } from "@/features/auth/mpin-keypad";
import { useIsDesktop } from "@/hooks/useMediaQuery";
import { DEMO_CREDENTIALS } from "@/mock/db";

/**
 * Client-side auth gate shown right before the fraud pipeline runs — mirrors
 * Global IME's "confirm with mPIN / Face / Fingerprint" step. Full-screen on
 * mobile, a centered modal card on laptop.
 */
export function TxnAuthStep({
  open,
  amountLabel,
  onSuccess,
  onCancel,
}: {
  open: boolean;
  amountLabel?: string;
  onSuccess: () => void;
  onCancel: () => void;
}) {
  const isDesktop = useIsDesktop();
  const [mode, setMode] = React.useState<"pin" | "bio">("pin");
  const [pin, setPin] = React.useState("");
  const [error, setError] = React.useState(false);

  React.useEffect(() => {
    if (open) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setPin("");
      setError(false);
      setMode("pin");
    }
  }, [open]);

  const checkPin = (value: string) => {
    if (value === DEMO_CREDENTIALS.mpin) {
      onSuccess();
      return;
    }
    setError(true);
    toast.error("Incorrect mPIN. Try again.");
    setTimeout(() => {
      setPin("");
      setError(false);
    }, 400);
  };

  if (!open) return null;

  const body = (
    <div className="flex flex-col items-center py-4 text-center">
      <h2 className="text-lg font-semibold">
        {mode === "pin" ? "Enter mPIN to Confirm" : "Biometric Verification"}
      </h2>
      {amountLabel && (
        <p className="mt-1 text-sm text-muted-foreground">{amountLabel}</p>
      )}

      <div className="mt-6 w-full">
        {mode === "pin" ? (
          <MpinKeypad
            value={pin}
            onChange={setPin}
            onComplete={checkPin}
            error={error}
            onBiometric={() => setMode("bio")}
          />
        ) : (
          <BiometricPrompt onSuccess={onSuccess} onCancel={() => setMode("pin")} />
        )}
      </div>

      {mode === "pin" && (
        <button
          onClick={() => setMode("bio")}
          className="mt-6 text-sm font-medium text-primary hover:underline"
        >
          Use Face ID / Fingerprint instead
        </button>
      )}
    </div>
  );

  if (isDesktop) {
    return (
      <Dialog open={open} onOpenChange={(v) => !v && onCancel()}>
        <DialogContent className="sm:max-w-sm">{body}</DialogContent>
      </Dialog>
    );
  }

  return (
    <div className="fixed inset-0 z-50 flex flex-col bg-background">
      <div className="flex items-center justify-end p-4">
        <button
          onClick={onCancel}
          className="flex h-9 w-9 items-center justify-center rounded-full text-muted-foreground transition-colors hover:bg-accent"
        >
          <X className="h-4 w-4" />
        </button>
      </div>
      <div className="flex flex-1 items-center justify-center px-6 pb-16">
        {body}
      </div>
    </div>
  );
}
