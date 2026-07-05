"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { motion } from "framer-motion";
import {
  ArrowRight,
  CheckCircle2,
  Loader2,
  ShieldAlert,
  ShieldCheck,
  ShieldQuestion,
} from "lucide-react";
import { toast } from "sonner";
import { Brand } from "@/components/shared/brand";
import { ThemeToggle } from "@/components/theme-toggle";
import { getDemoProfiles, loginWithProfile } from "@/services/authService";
import { ApiError } from "@/services/http";
import { initials } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { DemoProfile } from "@/lib/auth";

const EXPECTED_META: Record<
  DemoProfile["expected"],
  { icon: typeof ShieldCheck; label: string; className: string; dot: string }
> = {
  PASS: {
    icon: ShieldCheck,
    label: "",
    className: "text-success",
    dot: "",
  },
  OTP: {
    icon: ShieldQuestion,
    label: "OTP step-up",
    className: "text-warning",
    dot: "bg-warning",
  },
  BLOCK: {
    icon: ShieldAlert,
    label: "Blocked",
    className: "text-destructive",
    dot: "bg-destructive",
  },
};

export function LoginView() {
  const router = useRouter();
  const [pending, setPending] = React.useState<string | null>(null);
  const { data: profiles, isLoading, isError, refetch } = useQuery({
    queryKey: ["demo-profiles"],
    queryFn: getDemoProfiles,
  });

  const pick = async (profile: DemoProfile) => {
    setPending(profile.id);
    try {
      await loginWithProfile(profile.id);
      toast.success(`Signed in as ${profile.name}`);
      router.push("/dashboard");
    } catch (err) {
      toast.error(
        err instanceof ApiError ? err.message : "Could not sign in. Try again.",
      );
      setPending(null);
    }
  };

  return (
    <div className="grid min-h-screen lg:grid-cols-2">
      {/* Brand panel */}
      <div className="relative hidden flex-col justify-between overflow-hidden bg-sidebar p-12 text-sidebar-foreground lg:flex">
        <div
          className="pointer-events-none absolute inset-0 opacity-[0.07]"
          style={{
            backgroundImage:
              "radial-gradient(circle at 1px 1px, white 1px, transparent 0)",
            backgroundSize: "24px 24px",
          }}
        />
        <Brand invert subtitle />
        <div className="relative z-10 max-w-md space-y-6">
          <h1 className="text-4xl font-semibold leading-tight tracking-tight text-white">
            Smart banking, secured in real time.
          </h1>
          <p className="text-sidebar-foreground/70">
            Pick a demo profile to see the fraud-detection pipeline decide a
            live transaction — cleared, challenged with OTP, or blocked — end to
            end.
          </p>
          <div className="grid grid-cols-3 gap-4 pt-4">
            {[
              { k: "4", v: "AI Agents" },
              { k: "<800ms", v: "Decision" },
              { k: "Live", v: "Kafka" },
            ].map((s) => (
              <div key={s.v}>
                <div className="text-2xl font-semibold text-white">{s.k}</div>
                <div className="text-xs uppercase tracking-wider text-sidebar-foreground/50">
                  {s.v}
                </div>
              </div>
            ))}
          </div>
        </div>
        <div className="relative z-10 flex items-center gap-2 text-sm text-sidebar-foreground/60">
          <ShieldCheck className="h-4 w-4" />
          256-bit encryption · NRB compliant · Global IME Bank
        </div>
      </div>

      {/* Profile picker */}
      <div className="relative flex items-center justify-center p-6 sm:p-12">
        <div className="absolute right-6 top-6 flex items-center gap-2">
          <ThemeToggle />
        </div>
        <div className="w-full max-w-md">
          <div className="mb-8 lg:hidden">
            <Brand />
          </div>

          <h2 className="text-2xl font-semibold tracking-tight">
            Choose a profile
          </h2>
          <p className="mt-1.5 text-sm text-muted-foreground">
            Each profile logs in instantly and pre-loads a transaction that
            exercises a different fraud outcome.
          </p>

          <div className="mt-8 space-y-3">
            {isLoading &&
              Array.from({ length: 3 }).map((_, i) => (
                <div
                  key={i}
                  className="h-[92px] animate-pulse rounded-xl border bg-muted/40"
                />
              ))}

            {isError && (
              <div className="rounded-xl border border-destructive/30 bg-destructive/5 p-4 text-sm">
                <p className="font-medium text-destructive">
                  Couldn&apos;t reach the backend.
                </p>
                <p className="mt-1 text-muted-foreground">
                  Make sure the API is running, then{" "}
                  <button
                    onClick={() => refetch()}
                    className="font-medium text-primary hover:underline"
                  >
                    retry
                  </button>
                  .
                </p>
              </div>
            )}

            {profiles?.map((profile, i) => {
              const meta = EXPECTED_META[profile.expected];
              const busy = pending === profile.id;
              return (
                <motion.button
                  key={profile.id}
                  initial={{ opacity: 0, y: 8 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ delay: i * 0.06 }}
                  disabled={!!pending}
                  onClick={() => pick(profile)}
                  className={cn(
                    "group flex w-full items-center gap-4 rounded-xl border bg-card p-4 text-left transition-all",
                    "hover:border-primary/40 hover:shadow-sm disabled:opacity-60",
                    busy && "border-primary/40",
                  )}
                >
                  <div className="flex h-12 w-12 shrink-0 items-center justify-center rounded-full bg-primary/10 text-sm font-semibold text-primary">
                    {initials(profile.name)}
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span className="truncate text-sm font-semibold">
                        {profile.name}
                      </span>
                      {/* <span
                        className={cn(
                          "inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-medium",
                          "bg-muted",
                          meta.className,
                        )}
                      >
                        <span className={cn("h-1.5 w-1.5 rounded-full", meta.dot)} />
                        {meta.label}
                      </span> */}
                    </div>
                    {/* <p className="mt-0.5 truncate text-xs text-muted-foreground">
                      {profile.blurb}
                    </p> */}
                    <p className="mt-0.5 font-mono text-[10px] text-muted-foreground/70">
                      {profile.accountId}
                    </p>
                  </div>
                  {busy ? (
                    <Loader2 className="h-5 w-5 shrink-0 animate-spin text-primary" />
                  ) : (
                    <ArrowRight className="h-5 w-5 shrink-0 text-muted-foreground transition-transform group-hover:translate-x-0.5 group-hover:text-primary" />
                  )}
                </motion.button>
              );
            })}
          </div>

          {/* <div className="mt-6 flex items-start gap-2 rounded-lg bg-muted/40 p-3 text-xs text-muted-foreground">

          </div> */}

          <p className="mt-6 text-center text-xs text-muted-foreground">
            Bank staff?{" "}
            <a
              href="/admin"
              className="font-medium text-primary hover:underline"
            >
              Open Admin Portal
            </a>
          </p>
        </div>
      </div>
    </div>
  );
}
