"use client";

import * as React from "react";
import { motion } from "framer-motion";
import { Fingerprint } from "lucide-react";
import { cn } from "@/lib/utils";

const KEYS = ["1", "2", "3", "4", "5", "6", "7", "8", "9", "bio", "0", "back"] as const;

export function MpinKeypad({
  length = 4,
  value,
  onChange,
  onComplete,
  disabled,
  error,
  onBiometric,
}: {
  length?: number;
  value: string;
  onChange: (value: string) => void;
  onComplete?: (value: string) => void;
  disabled?: boolean;
  error?: boolean;
  onBiometric?: () => void;
}) {
  const press = (key: (typeof KEYS)[number]) => {
    if (disabled) return;
    if (key === "back") {
      onChange(value.slice(0, -1));
      return;
    }
    if (key === "bio") {
      onBiometric?.();
      return;
    }
    if (value.length >= length) return;
    const next = value + key;
    onChange(next);
    if (next.length === length) onComplete?.(next);
  };

  return (
    <div className="mx-auto w-full max-w-xs">
      <div className="mb-8 flex justify-center gap-3.5">
        {Array.from({ length }).map((_, i) => (
          <motion.span
            key={i}
            animate={error ? { x: [0, -6, 6, -6, 0] } : {}}
            transition={{ duration: 0.35 }}
            className={cn(
              "h-3.5 w-3.5 rounded-full border-2 transition-colors",
              error
                ? "border-destructive bg-destructive/20"
                : i < value.length
                  ? "border-primary bg-primary"
                  : "border-muted-foreground/30 bg-transparent",
            )}
          />
        ))}
      </div>

      <div className="grid grid-cols-3 gap-x-6 gap-y-4">
        {KEYS.map((key) => {
          if (key === "bio") {
            return (
              <button
                key={key}
                type="button"
                disabled={disabled || !onBiometric}
                onClick={() => press(key)}
                className="flex h-16 w-16 items-center justify-center justify-self-center rounded-full text-muted-foreground transition-colors hover:bg-accent disabled:opacity-0"
              >
                <Fingerprint className="h-6 w-6" />
              </button>
            );
          }
          if (key === "back") {
            return (
              <button
                key={key}
                type="button"
                disabled={disabled}
                onClick={() => press(key)}
                className="flex h-16 w-16 items-center justify-center justify-self-center rounded-full text-foreground transition-colors hover:bg-accent disabled:opacity-40"
              >
                <BackIcon />
              </button>
            );
          }
          return (
            <button
              key={key}
              type="button"
              disabled={disabled}
              onClick={() => press(key)}
              className="flex h-16 w-16 items-center justify-center justify-self-center rounded-full text-2xl font-medium text-foreground transition-colors hover:bg-accent active:bg-accent disabled:opacity-40"
            >
              {key}
            </button>
          );
        })}
      </div>
    </div>
  );
}

function BackIcon() {
  return (
    <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <path d="M21 4H8l-7 8 7 8h13a1 1 0 0 0 1-1V5a1 1 0 0 0-1-1Z" strokeLinejoin="round" />
      <path d="m14 9-4 4m0-4 4 4" strokeLinecap="round" />
    </svg>
  );
}
