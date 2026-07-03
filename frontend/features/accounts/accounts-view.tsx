"use client";

import { useRouter } from "next/navigation";
import { Landmark, PiggyBank, Wallet } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useAccounts } from "@/hooks/useBanking";
import { useAuth } from "@/lib/auth";
import { useBalanceVisibility } from "@/lib/balance-visibility";
import { formatNPR, maskAccount } from "@/lib/format";
import type { Account, AccountType } from "@/types/banking";

const accountIcons: Record<AccountType, typeof Landmark> = {
  savings: Landmark,
  current: Wallet,
  fixed_deposit: PiggyBank,
};

const accountTypeLabels: Record<AccountType, string> = {
  savings: "Savings",
  current: "Current",
  fixed_deposit: "Fixed Deposit",
};

export function AccountsView() {
  const { user } = useAuth();
  const router = useRouter();
  const { isVisible } = useBalanceVisibility();
  const { data: accounts, isLoading } = useAccounts(user?.customerId);

  const total = accounts?.reduce((sum, a) => sum + a.balance, 0) ?? 0;

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">My Accounts</h1>
        <p className="text-sm text-muted-foreground">
          All your Global IME accounts in one place.
        </p>
      </div>

      <Card className="border-primary/20 bg-primary/5">
        <CardContent className="flex items-center justify-between p-5">
          <div>
            <div className="text-xs uppercase tracking-wider text-muted-foreground">
              Total Balance
            </div>
            <div className="mt-1 text-2xl font-semibold tabular-nums">
              {isVisible ? formatNPR(total) : "NPR ••••••••"}
            </div>
          </div>
          <div className="flex h-11 w-11 items-center justify-center rounded-full bg-primary text-primary-foreground">
            <Landmark className="h-5 w-5" />
          </div>
        </CardContent>
      </Card>

      {isLoading ? (
        <div className="space-y-3">
          {Array.from({ length: 3 }).map((_, i) => (
            <Skeleton key={i} className="h-20" />
          ))}
        </div>
      ) : (
        <div className="space-y-3">
          {accounts?.map((acc) => (
            <AccountRow key={acc.id} account={acc} isVisible={isVisible} onOpen={() => router.push("/history")} />
          ))}
        </div>
      )}
    </div>
  );
}

function AccountRow({
  account,
  isVisible,
  onOpen,
}: {
  account: Account;
  isVisible: boolean;
  onOpen: () => void;
}) {
  const Icon = accountIcons[account.type];
  return (
    <button onClick={onOpen} className="w-full text-left">
      <Card className="transition-shadow hover:shadow-sm">
        <CardContent className="flex items-center gap-4 p-4">
          <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full bg-primary/10 text-primary">
            <Icon className="h-5 w-5" />
          </div>
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2">
              <span className="truncate text-sm font-semibold">{account.name}</span>
              <Badge variant="secondary" className="shrink-0">
                {accountTypeLabels[account.type]}
              </Badge>
              {account.status !== "active" && (
                <Badge variant="warning" className="shrink-0 capitalize">
                  {account.status}
                </Badge>
              )}
            </div>
            <div className="mt-0.5 font-mono text-xs text-muted-foreground">
              {maskAccount(account.accountNumber)}
            </div>
          </div>
          <div className="text-right">
            <div className="text-sm font-semibold tabular-nums">
              {isVisible ? formatNPR(account.balance) : "••••••"}
            </div>
            {account.type === "fixed_deposit" && (
              <div className="text-[11px] text-muted-foreground">
                {account.interestRate}% p.a.
              </div>
            )}
          </div>
        </CardContent>
      </Card>
    </button>
  );
}
