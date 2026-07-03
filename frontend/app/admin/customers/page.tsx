"use client";

import * as React from "react";
import { Search } from "lucide-react";
import { PageHeader } from "@/components/admin/page-header";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useAllCustomers } from "@/hooks/useAdmin";
import { formatDate, initials } from "@/lib/format";

const riskVariant = {
  low: "success",
  medium: "warning",
  high: "destructive",
} as const;

const kycVariant = {
  verified: "success",
  pending: "warning",
  rejected: "destructive",
} as const;

export default function CustomersPage() {
  const { data, isLoading } = useAllCustomers();
  const [search, setSearch] = React.useState("");

  const filtered = data?.filter(
    (c) =>
      c.name.toLowerCase().includes(search.toLowerCase()) ||
      c.accountNumber.includes(search) ||
      c.mobile.includes(search),
  );

  return (
    <div>
      <PageHeader
        title="Customers"
        description={`${data?.length ?? 0} registered customers`}
      />

      <Card className="mb-4">
        <CardContent className="p-4">
          <div className="relative">
            <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
            <Input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search by name, account or mobile"
              className="pl-9"
            />
          </div>
        </CardContent>
      </Card>

      <div className="overflow-hidden rounded-xl border bg-card">
        <Table>
          <TableHeader>
            <TableRow className="hover:bg-transparent">
              <TableHead>Customer</TableHead>
              <TableHead>Account</TableHead>
              <TableHead>Mobile</TableHead>
              <TableHead>Branch</TableHead>
              <TableHead>KYC</TableHead>
              <TableHead>Risk</TableHead>
              <TableHead>Joined</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {isLoading
              ? Array.from({ length: 10 }).map((_, i) => (
                  <TableRow key={i}>
                    {Array.from({ length: 7 }).map((__, j) => (
                      <TableCell key={j}>
                        <Skeleton className="h-4 w-full" />
                      </TableCell>
                    ))}
                  </TableRow>
                ))
              : filtered?.slice(0, 60).map((c) => (
                  <TableRow key={c.id}>
                    <TableCell>
                      <div className="flex items-center gap-2.5">
                        <Avatar className="h-8 w-8">
                          <AvatarFallback
                            style={{
                              background: `${c.avatarColor}20`,
                              color: c.avatarColor,
                            }}
                          >
                            {initials(c.name)}
                          </AvatarFallback>
                        </Avatar>
                        <div>
                          <div className="text-sm font-medium">{c.name}</div>
                          <div className="text-xs text-muted-foreground">
                            {c.id}
                          </div>
                        </div>
                      </div>
                    </TableCell>
                    <TableCell className="font-mono text-xs">
                      {c.accountNumber}
                    </TableCell>
                    <TableCell className="text-sm">{c.mobile}</TableCell>
                    <TableCell className="text-sm text-muted-foreground">
                      {c.branch}
                    </TableCell>
                    <TableCell>
                      <Badge variant={kycVariant[c.kycStatus]}>
                        {c.kycStatus}
                      </Badge>
                    </TableCell>
                    <TableCell>
                      <Badge variant={riskVariant[c.riskLevel]}>
                        {c.riskLevel}
                      </Badge>
                    </TableCell>
                    <TableCell className="text-xs text-muted-foreground">
                      {formatDate(c.joinedAt)}
                    </TableCell>
                  </TableRow>
                ))}
          </TableBody>
        </Table>
      </div>
    </div>
  );
}
