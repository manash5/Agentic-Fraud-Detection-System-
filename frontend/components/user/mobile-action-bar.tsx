"use client";

import Link from "next/link";
import { QrCode, Send } from "lucide-react";

/** Sticky quick-action bar shown above the bottom nav on the Home tab only. */
export function MobileActionBar() {
  return (
    <div className="flex gap-2 border-t bg-card/95 px-3 py-2 backdrop-blur">
      <Link
        href="/transfer/bank"
        className="flex flex-1 items-center justify-center gap-2 rounded-lg bg-primary py-2.5 text-sm font-semibold text-primary-foreground shadow-sm active:opacity-90"
      >
        <Send className="h-4 w-4" /> Fund Transfer
      </Link>
      <Link
        href="/transfer/wallet"
        className="flex flex-1 items-center justify-center gap-2 rounded-lg border border-primary/30 py-2.5 text-sm font-semibold text-primary active:bg-primary/5"
      >
        <QrCode className="h-4 w-4" /> Scan QR
      </Link>
    </div>
  );
}
