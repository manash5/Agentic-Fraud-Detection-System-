"use client";

import { motion } from "framer-motion";
import { Headphones, Home, ShieldAlert } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";

export function TransferBlocked({
  reference,
  onDone,
}: {
  reference: string;
  onDone: () => void;
}) {
  return (
    <div className="mx-auto max-w-md py-8 text-center">
      <motion.div
        initial={{ scale: 0.6, opacity: 0 }}
        animate={{ scale: 1, opacity: 1 }}
        transition={{ type: "spring", stiffness: 200, damping: 16 }}
        className="mx-auto mb-6 flex h-20 w-20 items-center justify-center rounded-full bg-destructive/12"
      >
        <ShieldAlert className="h-9 w-9 text-destructive" />
      </motion.div>

      <h2 className="text-xl font-semibold">Transaction Failed</h2>
      <p className="mx-auto mt-2 max-w-sm text-sm text-muted-foreground">
        For your security, this transaction has been temporarily blocked. No
        amount has been debited from your account.
      </p>

      <Card className="mt-6 text-left">
        <CardContent className="space-y-3 p-5 text-sm">
          <div className="flex justify-between">
            <span className="text-muted-foreground">Reference No.</span>
            <span className="font-mono text-xs font-medium">{reference}</span>
          </div>
          <div className="flex justify-between">
            <span className="text-muted-foreground">Status</span>
            <span className="font-medium text-destructive">Blocked</span>
          </div>
          <div className="rounded-lg bg-muted/50 p-3 text-xs text-muted-foreground">
            If you believe this is a mistake, please contact our 24/7 support
            with the reference number above.
          </div>
        </CardContent>
      </Card>

      <div className="mt-6 space-y-3">
        <Button variant="outline" className="w-full">
          <Headphones className="h-4 w-4" /> Contact Bank · 16600122233
        </Button>
        <Button className="w-full" onClick={onDone}>
          <Home className="h-4 w-4" /> Back to Home
        </Button>
      </div>
    </div>
  );
}
