"use client";

import * as React from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { motion } from "framer-motion";
import {
  ArrowRight,
  Globe,
  Grid3x3,
  Landmark,
  PiggyBank,
  type LucideIcon,
} from "lucide-react";
import { toast } from "sonner";
import { Brand } from "@/components/shared/brand";
import { BrandLogo, type BrandLogoId } from "@/components/shared/brand-logo";
import { TransactionItem } from "@/components/shared/transaction-item";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useAccounts, useTransactions } from "@/hooks/useBanking";
import { useAuth } from "@/lib/auth";
import { useBalanceVisibility } from "@/lib/balance-visibility";
import { formatNPR, maskAccount } from "@/lib/format";
import { cn } from "@/lib/utils";

const quickActions: {
  label: string;
  logo?: BrandLogoId;
  icon?: LucideIcon;
  action: (router: ReturnType<typeof useRouter>) => void;
}[] = [
  {
    label: "Load Wallet",
    logo: "esewa",
    action: (router) => router.push("/transfer/wallet"),
  },
  {
    label: "Fixed Deposit",
    icon: PiggyBank,
    action: () => toast.info("Fixed Deposit application coming soon"),
  },
  {
    label: "Digital Universe",
    icon: Globe,
    action: () => toast.info("Digital Universe coming soon"),
  },
  {
    label: "View All",
    icon: Grid3x3,
    action: (router) => router.push("/hub"),
  },
];

const favourites = [
  { name: "Sabin K.", account: "1201020200112233" },
  { name: "Nisha R.", account: "1201030300445566" },
  { name: "eSewa", account: "9801234567", logo: "esewa" as const },
  { name: "Khalti", account: "9807654321", logo: "khalti" as const },
];

export function DashboardView() {
  const { user } = useAuth();
  const router = useRouter();
  const { isVisible } = useBalanceVisibility();
  const { data: accounts, isLoading: accountsLoading } = useAccounts(
    user?.customerId,
  );
  const { data: transactions, isLoading: txnLoading } = useTransactions({
    customerId: user?.customerId,
    limit: 5,
  });

  const savings = accounts?.find((a) => a.type === "savings");

  return (
    <div className="space-y-6">
      {/* Quick actions */}
      <div className="grid grid-cols-4 gap-3">
        {quickActions.map((action) => (
          <button
            key={action.label}
            onClick={() => action.action(router)}
            className="group flex flex-col items-center gap-2 rounded-xl border bg-card p-3 text-center transition-all hover:border-primary/30 hover:shadow-sm sm:p-4"
          >
            <div className="flex h-11 w-11 items-center justify-center overflow-hidden rounded-full bg-primary/10 transition-colors group-hover:bg-primary/5">
              {action.logo ? (
                <BrandLogo id={action.logo} size={36} />
              ) : action.icon ? (
                <action.icon className="h-5 w-5 text-primary transition-colors group-hover:text-primary" />
              ) : null}
            </div>
            <span className="text-[11px] font-medium leading-tight">
              {action.label}
            </span>
          </button>
        ))}
      </div>

      {/* Easy Balance card */}
      <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }}>
        <div className="relative overflow-hidden rounded-2xl bg-primary p-6 text-primary-foreground shadow-lg">
          <div
            className="pointer-events-none absolute -right-16 -top-16 h-56 w-56 rounded-full"
            style={{
              background:
                "radial-gradient(circle, color-mix(in oklch, white 35%, transparent), transparent 70%)",
            }}
          />
          <div className="relative z-10 flex items-start justify-between">
            <div>
              <div className="text-xs uppercase tracking-widest text-primary-foreground/60">
                Smart Savings Account
              </div>
              <div className="mt-2">
                {accountsLoading ? (
                  <Skeleton className="h-9 w-48 bg-white/15" />
                ) : (
                  <span className="text-3xl font-semibold tabular-nums">
                    {isVisible ? formatNPR(savings?.balance ?? 0) : "NPR ••••••••"}
                  </span>
                )}
              </div>
            </div>
            <Brand invert subtitle={false} />
          </div>
          <div className="relative z-10 mt-8 flex items-end justify-between">
            <div>
              <div className="text-[11px] uppercase tracking-wider text-primary-foreground/50">
                Account Number
              </div>
              <div className="mt-1 font-mono text-sm tracking-wide text-primary-foreground/90">
                {savings ? maskAccount(savings.accountNumber) : "—"}
              </div>
            </div>
            <Link
              href="/history"
              className="text-xs font-medium text-primary-foreground underline-offset-4 hover:underline"
            >
              View Statement
            </Link>
          </div>
        </div>
      </motion.div>

      {/* Compact account summary — desktop only, mobile has the Accounts tab */}
      <div className="hidden gap-3 lg:grid lg:grid-cols-3">
        {accountsLoading
          ? Array.from({ length: 3 }).map((_, i) => (
              <Skeleton key={i} className="h-20" />
            ))
          : accounts?.slice(0, 3).map((acc) => (
              <Card key={acc.id}>
                <CardContent className="flex items-center gap-3 p-4">
                  <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-primary/10 text-primary">
                    <Landmark className="h-5 w-5" />
                  </div>
                  <div className="min-w-0">
                    <div className="truncate text-xs text-muted-foreground">
                      {acc.name}
                    </div>
                    <div className="text-sm font-semibold tabular-nums">
                      {isVisible ? formatNPR(acc.balance) : "••••••"}
                    </div>
                  </div>
                </CardContent>
              </Card>
            ))}
      </div>

      {/* Favourites */}
      <div>
        <h2 className="mb-3 text-sm font-semibold text-muted-foreground">
          Favourites
        </h2>
        <div className="no-scrollbar flex gap-4 overflow-x-auto pb-1">
          {favourites.map((f) => (
            <button
              key={f.name}
              onClick={() =>
                router.push(
                  `/transfer/bank?account=${encodeURIComponent(f.account)}&name=${encodeURIComponent(f.name)}`,
                )
              }
              className="flex shrink-0 flex-col items-center gap-1.5"
            >
              <div className="flex h-12 w-12 items-center justify-center overflow-hidden rounded-full bg-primary/10">
                {"logo" in f && f.logo ? (
                  <BrandLogo id={f.logo} size={40} />
                ) : (
                  <span className="text-sm font-semibold text-primary">
                    {f.name
                      .split(" ")
                      .map((n) => n[0])
                      .join("")}
                  </span>
                )}
              </div>
              <span className="text-[11px] font-medium text-muted-foreground">
                {f.name}
              </span>
            </button>
          ))}
        </div>
      </div>

      {/* Easy History */}
      <Card>
        <CardContent className="p-4 sm:p-5">
          <div className="mb-2 flex items-center justify-between">
            <h2 className="text-sm font-semibold">Easy History</h2>
            <Link
              href="/history"
              className="flex items-center gap-1 text-xs font-medium text-primary hover:underline"
            >
              View all <ArrowRight className="h-3 w-3" />
            </Link>
          </div>
          {txnLoading ? (
            <div className="space-y-2">
              {Array.from({ length: 5 }).map((_, i) => (
                <div key={i} className="flex items-center gap-3 py-2">
                  <Skeleton className="h-10 w-10 rounded-full" />
                  <div className="flex-1 space-y-1.5">
                    <Skeleton className="h-3.5 w-32" />
                    <Skeleton className="h-3 w-24" />
                  </div>
                  <Skeleton className="h-4 w-16" />
                </div>
              ))}
            </div>
          ) : (
            <div className={cn("divide-y divide-border/60")}>
              {transactions?.map((txn) => (
                <TransactionItem
                  key={txn.id}
                  txn={txn}
                  onClick={() => router.push(`/history?txn=${txn.id}`)}
                />
              ))}
            </div>
          )}
          {!txnLoading && !transactions?.length && (
            <p className="py-6 text-center text-sm text-muted-foreground">
              No transactions yet.
            </p>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
