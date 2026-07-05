"use client";

// Minimal "processing" screen shown between submit and the terminal status.
// The per-agent breakdown lives in the admin console, not the customer flow.
import { motion } from "framer-motion";
import { Loader2, ShieldCheck } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { formatNPR } from "@/lib/format";

export function TransferProcessing({
  amount,
  recipient,
  slow,
}: {
  amount: number;
  recipient: string;
  slow?: boolean;
}) {
  return (
    <Card>
      <CardContent className="flex flex-col items-center py-12 text-center">
        <motion.div
          initial={{ scale: 0.8, opacity: 0 }}
          animate={{ scale: 1, opacity: 1 }}
          className="relative mb-6 flex h-20 w-20 items-center justify-center rounded-full bg-primary/10"
        >
          <Loader2 className="h-9 w-9 animate-spin text-primary" />
          <ShieldCheck className="absolute h-5 w-5 text-primary" />
        </motion.div>
        <h2 className="text-lg font-semibold">Securing your transfer</h2>
        <p className="mt-2 max-w-xs text-sm text-muted-foreground">
          Running fraud checks on your {formatNPR(amount)} transfer
          {recipient ? ` to ${recipient}` : ""}…
        </p>
        <div className="mt-4 flex items-center gap-1.5">
          {[0, 1, 2].map((i) => (
            <motion.span
              key={i}
              className="h-1.5 w-1.5 rounded-full bg-primary"
              animate={{ opacity: [0.3, 1, 0.3] }}
              transition={{ duration: 1.2, repeat: Infinity, delay: i * 0.2 }}
            />
          ))}
        </div>
        {slow && (
          <p className="mt-6 rounded-md bg-warning/10 px-3 py-2 text-xs text-warning">
            Taking longer than usual — the pipeline may be under load.
          </p>
        )}
      </CardContent>
    </Card>
  );
}
