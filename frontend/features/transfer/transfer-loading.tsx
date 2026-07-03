"use client";

import { Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";

/** In-container spinner after mPIN — overlays the form card, not the full viewport. */
export function TransferLoadingOverlay({ className }: { className?: string }) {
  return (
    <div
      className={cn(
        "absolute inset-0 z-20 flex items-center justify-center rounded-xl bg-background/80 backdrop-blur-[2px]",
        className,
      )}
    >
      <Loader2 className="h-10 w-10 animate-spin text-primary" />
    </div>
  );
}
