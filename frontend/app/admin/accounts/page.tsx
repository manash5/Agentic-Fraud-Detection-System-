"use client";

import { PageHeader } from "@/components/admin/page-header";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useAllAccounts } from "@/hooks/useAdmin";
import { formatNPR } from "@/lib/format";

const typeLabel = {
  savings: "Savings",
  current: "Current",
  fixed_deposit: "Fixed Deposit",
} as const;

const statusVariant = {
  active: "success",
  dormant: "warning",
  frozen: "destructive",
} as const;

export default function AccountsPage() {
  const { data, isLoading } = useAllAccounts();

  const totalBalance = data?.reduce((s, a) => s + a.balance, 0) ?? 0;

  return (
    <div>
      <PageHeader
        title="Accounts"
        description={`${data?.length ?? 0} accounts · ${formatNPR(totalBalance)} under management`}
      />

      <div className="overflow-hidden rounded-xl border bg-card">
        <Table>
          <TableHeader>
            <TableRow className="hover:bg-transparent">
              <TableHead>Account Number</TableHead>
              <TableHead>Type</TableHead>
              <TableHead>Product</TableHead>
              <TableHead className="text-right">Balance</TableHead>
              <TableHead className="text-right">Interest</TableHead>
              <TableHead>Status</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {isLoading
              ? Array.from({ length: 12 }).map((_, i) => (
                  <TableRow key={i}>
                    {Array.from({ length: 6 }).map((__, j) => (
                      <TableCell key={j}>
                        <Skeleton className="h-4 w-full" />
                      </TableCell>
                    ))}
                  </TableRow>
                ))
              : data?.slice(0, 80).map((a) => (
                  <TableRow key={a.id}>
                    <TableCell className="font-mono text-xs">
                      {a.accountNumber}
                    </TableCell>
                    <TableCell>
                      <Badge variant="secondary">{typeLabel[a.type]}</Badge>
                    </TableCell>
                    <TableCell className="text-sm">{a.name}</TableCell>
                    <TableCell className="text-right font-medium tabular-nums">
                      {formatNPR(a.balance, false)}
                    </TableCell>
                    <TableCell className="text-right text-sm text-muted-foreground">
                      {a.interestRate ? `${a.interestRate}%` : "—"}
                    </TableCell>
                    <TableCell>
                      <Badge variant={statusVariant[a.status]}>
                        {a.status}
                      </Badge>
                    </TableCell>
                  </TableRow>
                ))}
          </TableBody>
        </Table>
      </div>
    </div>
  );
}
