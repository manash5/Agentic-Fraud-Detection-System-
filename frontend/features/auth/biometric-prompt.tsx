"use client";

import * as React from "react";
import { motion } from "framer-motion";
import { Check, Fingerprint, X } from "lucide-react";
import { cn } from "@/lib/utils";

type ScanState = "scanning" | "success" | "failed";

/**
 * Mock Face ID / fingerprint scan. Auto-succeeds after a short delay to
 * simulate biometric hardware — real devices would call the WebAuthn API.
 */
export function BiometricPrompt({
  onSuccess,
  onCancel,
  label = "Scan your fingerprint to continue",
}: {
  onSuccess: () => void;
  onCancel: () => void;
  label?: string;
}) {
  const [state, setState] = React.useState<ScanState>("scanning");

  React.useEffect(() => {
    const timer = setTimeout(() => {
      setState("success");
      setTimeout(onSuccess, 550);
    }, 1200);
    return () => clearTimeout(timer);
  }, [onSuccess]);

  return (
    <div className="flex flex-col items-center py-10 text-center">
      <motion.div
        className={cn(
          "relative flex h-28 w-28 items-center justify-center rounded-full",
          state === "success" ? "bg-success/15" : "bg-primary/10",
        )}
      >
        {state === "scanning" && (
          <motion.div
            className="absolute inset-0 rounded-full border-2 border-primary/30"
            animate={{ scale: [1, 1.25, 1], opacity: [0.7, 0, 0.7] }}
            transition={{ duration: 1.4, repeat: Infinity }}
          />
        )}
        {state === "success" ? (
          <motion.div
            initial={{ scale: 0 }}
            animate={{ scale: 1 }}
            transition={{ type: "spring" }}
            className="flex h-16 w-16 items-center justify-center rounded-full bg-success text-success-foreground"
          >
            <Check className="h-8 w-8" strokeWidth={3} />
          </motion.div>
        ) : state === "failed" ? (
          <X className="h-10 w-10 text-destructive" />
        ) : (
          <Fingerprint className="h-12 w-12 text-primary" />
        )}
      </motion.div>

      <p className="mt-6 text-sm font-medium text-foreground">
        {state === "success" ? "Verified" : label}
      </p>
      <p className="mt-1 text-xs text-muted-foreground">
        {state === "scanning" ? "Hold still…" : "Redirecting…"}
      </p>

      {state === "scanning" && (
        <button
          onClick={onCancel}
          className="mt-8 text-sm font-medium text-muted-foreground hover:text-foreground"
        >
          Cancel
        </button>
      )}
    </div>
  );
}
