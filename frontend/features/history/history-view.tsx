"use client";

import * as React from "react";
import { useSearchParams } from "next/navigation";
import { Search, SlidersHorizontal } from "lucide-react";
import { TransactionItem } from "@/components/shared/transaction-item";
import { TxnDetailSheet } from "./txn-detail-sheet";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useTransactions } from "@/hooks/useBanking";
import { useAuth } from "@/lib/auth";
import { formatDate } from "@/lib/format";
import type { Transaction, TransactionType } from "@/types/banking";

export function HistoryView() {
  const { user } = useAuth();
  const params = useSearchParams();
  const [search, setSearch] = React.useState("");
  const [type, setType] = React.useState<TransactionType | "all">("all");
  const [range, setRange] = React.useState("all");
  const [selected, setSelected] = React.useState<Transaction | null>(null);
  const [open, setOpen] = React.useState(false);

  const from = React.useMemo(() => {
    if (range === "all") return undefined;
    // eslint-disable-next-line react-hooks/purity
    return new Date(Date.now() - Number(range) * 86400000).toISOString();
  }, [range]);

  const { data, isLoading } = useTransactions({
    customerId: user?.customerId,
    search: search || undefined,
    type,
    from,
  });

  // Deep-link open from dashboard (?txn=)
  const txnId = params.get("txn");
  React.useEffect(() => {
    if (txnId && data) {
      const found = data.find((t) => t.id === txnId);
      if (found) {
        // eslint-disable-next-line react-hooks/set-state-in-effect
        setSelected(found);
        setOpen(true);
      }
    }
  }, [txnId, data]);

  const grouped = React.useMemo(() => groupByDate(data ?? []), [data]);

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">
          Transaction History
        </h1>
        <p className="text-sm text-muted-foreground">
          Search and filter all your account activity.
        </p>
      </div>

      <Card>
        <CardContent className="flex flex-col gap-3 p-4 sm:flex-row sm:items-center">
          <div className="relative flex-1">
            <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
            <Input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search by name, reference or ID"
              className="pl-9"
            />
          </div>
          <Select
            value={type}
            onValueChange={(v) => setType(v as TransactionType | "all")}
          >
            <SelectTrigger className="sm:w-40">
              <SlidersHorizontal className="h-4 w-4" />
              <SelectValue placeholder="Type" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All Types</SelectItem>
              <SelectItem value="transfer">Transfer</SelectItem>
              <SelectItem value="payment">Payment</SelectItem>
              <SelectItem value="qr_payment">QR Payment</SelectItem>
              <SelectItem value="topup">Wallet Top-up</SelectItem>
              <SelectItem value="deposit">Deposit</SelectItem>
              <SelectItem value="withdrawal">Withdrawal</SelectItem>
            </SelectContent>
          </Select>
          <Select value={range} onValueChange={setRange}>
            <SelectTrigger className="sm:w-36">
              <SelectValue placeholder="Date" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All time</SelectItem>
              <SelectItem value="7">Last 7 days</SelectItem>
              <SelectItem value="30">Last 30 days</SelectItem>
              <SelectItem value="90">Last 90 days</SelectItem>
            </SelectContent>
          </Select>
        </CardContent>
      </Card>

      {isLoading ? (
        <Card>
          <CardContent className="space-y-2 p-4">
            {Array.from({ length: 8 }).map((_, i) => (
              <div key={i} className="flex items-center gap-3 py-2">
                <Skeleton className="h-10 w-10 rounded-full" />
                <div className="flex-1 space-y-1.5">
                  <Skeleton className="h-3.5 w-40" />
                  <Skeleton className="h-3 w-28" />
                </div>
                <Skeleton className="h-4 w-16" />
              </div>
            ))}
          </CardContent>
        </Card>
      ) : grouped.length === 0 ? (
        <Card>
          <CardContent className="py-16 text-center text-sm text-muted-foreground">
            No transactions match your filters.
          </CardContent>
        </Card>
      ) : (
        <div className="space-y-4">
          {grouped.map(([date, txns]) => (
            <Card key={date}>
              <CardContent className="p-4">
                <div className="mb-1 px-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                  {date}
                </div>
                <div className="divide-y divide-border/60">
                  {txns.map((txn) => (
                    <TransactionItem
                      key={txn.id}
                      txn={txn}
                      onClick={() => {
                        setSelected(txn);
                        setOpen(true);
                      }}
                    />
                  ))}
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}

      <TxnDetailSheet txn={selected} open={open} onOpenChange={setOpen} />
    </div>
  );
}

function groupByDate(txns: Transaction[]): [string, Transaction[]][] {
  const map = new Map<string, Transaction[]>();
  txns.forEach((t) => {
    const key = formatDate(t.timestamp);
    const arr = map.get(key) ?? [];
    arr.push(t);
    map.set(key, arr);
  });
  return Array.from(map.entries());
}
