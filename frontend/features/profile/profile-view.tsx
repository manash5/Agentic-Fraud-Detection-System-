"use client";

import {
  BadgeCheck,
  ChevronRight,
  CreditCard,
  Fingerprint,
  Gauge,
  MapPin,
  Shield,
  Smartphone,
} from "lucide-react";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useAccounts, useCustomer, useTransactions } from "@/hooks/useBanking";
import { useAuth } from "@/lib/auth";
import { formatDate, formatNPR, initials, relativeTime } from "@/lib/format";

export function ProfileView() {
  const { user } = useAuth();
  const { data: customer, isLoading } = useCustomer(user?.customerId);
  const { data: accounts } = useAccounts(user?.customerId);
  const { data: history } = useTransactions({
    customerId: user?.customerId,
    limit: 100,
  });

  if (isLoading || !customer) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-32" />
        <Skeleton className="h-64" />
      </div>
    );
  }

  // Real devices observed on the account, from transaction history.
  const seen = new Map<string, { name: string; location: string; last: string }>();
  for (const t of history ?? []) {
    if (!t.deviceId || seen.has(t.deviceId)) continue;
    seen.set(t.deviceId, {
      name: t.deviceId,
      location: t.location.city,
      last: relativeTime(t.timestamp),
    });
  }
  const devices = [...seen.values()].slice(0, 4);

  return (
    <div className="space-y-5">
      <h1 className="text-xl font-semibold tracking-tight">Profile</h1>

      <Card>
        <CardContent className="flex items-center gap-4 p-5">
          <Avatar className="h-16 w-16 text-lg">
            <AvatarFallback>{initials(customer.name)}</AvatarFallback>
          </Avatar>
          <div className="flex-1">
            <div className="flex items-center gap-2">
              <h2 className="text-lg font-semibold">{customer.name}</h2>
              {customer.kycStatus === "verified" && (
                <BadgeCheck className="h-4 w-4 text-success" />
              )}
            </div>
            <p className="text-sm text-muted-foreground">{customer.email}</p>
            <p className="text-sm text-muted-foreground">{customer.mobile}</p>
          </div>
          <Badge variant="success" className="gap-1">
            <Shield className="h-3 w-3" /> KYC Verified
          </Badge>
        </CardContent>
      </Card>

      <div className="grid gap-4 sm:grid-cols-2">
        <Section title="Account Details" icon={CreditCard}>
          <Detail label="Account Number" value={customer.accountNumber} mono />
          <Detail label="Branch" value={customer.branch} />
          <Detail label="Member Since" value={formatDate(customer.joinedAt)} />
          <Detail
            label="Citizenship No."
            value={customer.citizenshipNo}
            mono
          />
        </Section>

        <Section title="Accounts & Limits" icon={Gauge}>
          {accounts?.map((a) => (
            <Detail
              key={a.id}
              label={a.name}
              value={formatNPR(a.balance)}
            />
          ))}
          <Detail label="Daily Transfer Limit" value={formatNPR(500000)} />
          <Detail label="Per Txn Limit" value={formatNPR(200000)} />
        </Section>

        <Section title="KYC Information" icon={Fingerprint}>
          <Detail label="Status" value="Verified" />
          <Detail label="Address" value={customer.address} />
          <Detail label="City" value={customer.city} />
          <Detail label="Risk Rating" value={customer.riskLevel.toUpperCase()} />
        </Section>

        <Section title="Linked Devices" icon={Smartphone}>
          {devices.map((d) => (
            <div
              key={d.name}
              className="flex items-center justify-between border-b py-2.5 last:border-0"
            >
              <div>
                <div className="text-sm font-medium">{d.name}</div>
                <div className="flex items-center gap-1 text-xs text-muted-foreground">
                  <MapPin className="h-3 w-3" /> {d.location} · {d.last}
                </div>
              </div>
              <ChevronRight className="h-4 w-4 text-muted-foreground" />
            </div>
          ))}
        </Section>
      </div>
    </div>
  );
}

function Section({
  title,
  icon: Icon,
  children,
}: {
  title: string;
  icon: typeof Shield;
  children: React.ReactNode;
}) {
  return (
    <Card>
      <CardContent className="p-5">
        <div className="mb-3 flex items-center gap-2">
          <Icon className="h-4 w-4 text-primary" />
          <h3 className="text-sm font-semibold">{title}</h3>
        </div>
        <div>{children}</div>
      </CardContent>
    </Card>
  );
}

function Detail({
  label,
  value,
  mono,
}: {
  label: string;
  value: string;
  mono?: boolean;
}) {
  return (
    <div className="flex items-center justify-between border-b py-2.5 text-sm last:border-0">
      <span className="text-muted-foreground">{label}</span>
      <span className={`font-medium ${mono ? "font-mono text-xs" : ""}`}>
        {value}
      </span>
    </div>
  );
}
