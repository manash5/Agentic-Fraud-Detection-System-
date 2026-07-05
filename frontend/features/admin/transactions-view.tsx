"use client";

import * as React from "react";
import { Search } from "lucide-react";
import { PageHeader } from "@/components/admin/page-header";
import { SubmissionExportButton } from "./submission-export";
import { TxnTable } from "./txn-table";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useAdminTransactions } from "@/hooks/useAdmin";
import type { TransactionType } from "@/types/banking";

export function AdminTransactionsView() {
  const [search, setSearch] = React.useState("");
  const [type, setType] = React.useState<TransactionType | "all">("all");
  const [decision, setDecision] = React.useState("all");

  const { data, isLoading } = useAdminTransactions({
    search: search || undefined,
    type,
    decision,
    limit: 200,
  });

  return (
    <div>
      <PageHeader
        title="Transactions"
        description="Browse and inspect every processed transaction."
        action={<SubmissionExportButton transactions={data ?? []} label="Export CSV" />}
      />

      <Card className="mb-4">
        <CardContent className="flex flex-col gap-3 p-4 sm:flex-row">
          <div className="relative flex-1">
            <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
            <Input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search by ID, reference or customer"
              className="pl-9"
            />
          </div>
          <Select
            value={type}
            onValueChange={(v) => setType(v as TransactionType | "all")}
          >
            <SelectTrigger className="sm:w-44">
              <SelectValue placeholder="Type" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All Types</SelectItem>
              <SelectItem value="transfer">Transfer</SelectItem>
              <SelectItem value="payment">Payment</SelectItem>
              <SelectItem value="qr_payment">QR Payment</SelectItem>
              <SelectItem value="topup">Wallet Top-up</SelectItem>
              <SelectItem value="withdrawal">Withdrawal</SelectItem>
            </SelectContent>
          </Select>
          <Select value={decision} onValueChange={setDecision}>
            <SelectTrigger className="sm:w-40">
              <SelectValue placeholder="Decision" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All Decisions</SelectItem>
              <SelectItem value="PASS">Pass</SelectItem>
              <SelectItem value="OTP">OTP</SelectItem>
              <SelectItem value="BLOCK">Block</SelectItem>
            </SelectContent>
          </Select>
        </CardContent>
      </Card>

      <TxnTable transactions={data} loading={isLoading} />
    </div>
  );
}
