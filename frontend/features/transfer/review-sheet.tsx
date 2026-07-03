"use client";

import type { ReactNode } from "react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { useIsDesktop } from "@/hooks/useMediaQuery";
import { cn } from "@/lib/utils";

/**
 * Responsive transaction review step — a bottom sheet on mobile ("Let's
 * Review!") and a centered dialog on laptop, sharing the same row content.
 */
export function ReviewSheet({
  open,
  onOpenChange,
  title = "Let's Review!",
  description,
  children,
  confirmLabel = "Confirm & Continue",
  onConfirm,
  confirmDisabled,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title?: string;
  description?: string;
  children: ReactNode;
  confirmLabel?: string;
  onConfirm: () => void;
  confirmDisabled?: boolean;
}) {
  const isDesktop = useIsDesktop();

  if (isDesktop) {
    return (
      <Dialog open={open} onOpenChange={onOpenChange}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>{title}</DialogTitle>
            {description && <DialogDescription>{description}</DialogDescription>}
          </DialogHeader>
          <div className="space-y-2 rounded-lg border bg-muted/30 p-4 text-sm">
            {children}
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button onClick={onConfirm} disabled={confirmDisabled}>
              {confirmLabel}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    );
  }

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="bottom" className="rounded-t-2xl">
        <SheetHeader>
          <SheetTitle>{title}</SheetTitle>
          {description && <SheetDescription>{description}</SheetDescription>}
        </SheetHeader>
        <div className="px-6">
          <div className="space-y-2 rounded-lg border bg-muted/30 p-4 text-sm">
            {children}
          </div>
        </div>
        <div className="space-y-2 p-6 pt-4">
          <Button className="w-full" onClick={onConfirm} disabled={confirmDisabled}>
            {confirmLabel}
          </Button>
          <Button variant="ghost" className="w-full" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
        </div>
      </SheetContent>
    </Sheet>
  );
}

export function ReviewRow({
  label,
  value,
  mono,
  strong,
  className,
}: {
  label: string;
  value: string;
  mono?: boolean;
  strong?: boolean;
  className?: string;
}) {
  return (
    <div className={cn("flex items-center justify-between gap-4", className)}>
      <span className="text-muted-foreground">{label}</span>
      <span
        className={cn(
          "text-right",
          mono && "font-mono text-xs",
          strong ? "font-semibold" : "font-medium",
        )}
      >
        {value}
      </span>
    </div>
  );
}
