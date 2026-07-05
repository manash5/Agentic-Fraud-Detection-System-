"use client";

import {
  CheckCircle2,
  Clock,
  Fingerprint,
  Globe,
  Loader2,
  MapPin,
  ShieldOff,
  Smartphone,
  User,
} from "lucide-react";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { DecisionBadge } from "@/components/shared/decision-badge";
import {
  AgentCards,
  ShapChart,
  SynthesisGauge,
} from "./fraud-analysis";
import { NetworkGraph } from "./network-graph";
import { useAdminCustomer, useModelVerdict } from "@/hooks/useAdmin";
import { formatDateTime, formatNPR, relativeTime } from "@/lib/format";
import { channelLabels, fraudTypeLabels, txnTypeLabels } from "@/lib/trackb";
import { typeLabels } from "@/lib/risk";
import { cn } from "@/lib/utils";
import type { Transaction } from "@/types/banking";

export function TxnDetailView({ txn }: { txn: Transaction }) {
  const { data: customer } = useAdminCustomer(txn.customerId);
  const { data: verdict, isLoading: verdictLoading } = useModelVerdict(txn.id);

  return (
    <div>
      <div className="mb-6 flex flex-wrap items-start justify-between gap-4 border-b pb-5">
        <div>
          <h1 className="font-mono text-lg font-semibold">{txn.id}</h1>
          <p className="text-sm text-muted-foreground">
            {formatDateTime(txn.timestamp)} · {relativeTime(txn.timestamp)}
          </p>
        </div>
        <DecisionBadge
          decision={txn.decision}
          trackB
          forcedByDisagreement={(txn.fraud?.synthesis.disagreement ?? 0) >= 0.04}
        />
      </div>

      <Tabs defaultValue="overview">
        <TabsList className="grid w-full grid-cols-2 sm:grid-cols-5">
          <TabsTrigger value="overview">Overview</TabsTrigger>
          <TabsTrigger value="customer">Customer</TabsTrigger>
          <TabsTrigger value="fraud">AI Analysis</TabsTrigger>
          <TabsTrigger value="timeline">Timeline</TabsTrigger>
          <TabsTrigger value="audit">Audit</TabsTrigger>
        </TabsList>

        <TabsContent value="overview" className="mt-4 space-y-4">
          <div className="rounded-xl border bg-muted/30 p-5 text-center">
            <div className="text-xs uppercase tracking-wider text-muted-foreground">
              Amount
            </div>
            <div className="mt-1 text-3xl font-semibold tabular-nums">
              {formatNPR(txn.amount)}
            </div>
            <div className="mt-1 text-sm text-muted-foreground">
              {typeLabels[txn.type]} · {txn.channel.toUpperCase()}
            </div>
          </div>

          <div className="grid gap-3 sm:grid-cols-2">
            <InfoCard icon={User} label="Sender" value={txn.customerName} sub={txn.accountNumber} />
            <InfoCard
              icon={User}
              label="Receiver"
              value={txn.counterparty.name}
              sub={`${txn.counterpartyId} · ${txn.counterparty.bank}`}
            />
            <InfoCard icon={MapPin} label="Location" value={txn.location.city} sub={`${txn.location.lat.toFixed(3)}, ${txn.location.lng.toFixed(3)}`} />
            <InfoCard icon={Smartphone} label="Device" value={txn.device} sub={txn.ipAddress} />
          </div>

          <div className="grid grid-cols-3 gap-3">
            <MiniStat label="Risk Score" value={txn.riskScore.toFixed(2)} />
            <MiniStat label="Decision" value={txn.decision} />
            <MiniStat label="Latency" value={`${Math.round(txn.latencyMs)}ms`} />
          </div>

          <div className="rounded-lg border p-4">
            <h3 className="mb-3 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
              Transaction metadata
            </h3>
            <div className="grid grid-cols-2 gap-x-4 gap-y-2 text-sm sm:grid-cols-3">
              <DetailRow label="txn_type" value={txnTypeLabels[txn.txnType]} mono />
              <DetailRow label="channel" value={channelLabels[toChannel(txn.channel)]} />
              <DetailRow label="auth_method" value={txn.authMethod} mono />
              <DetailRow label="counterparty_id" value={txn.counterpartyId} mono />
              <DetailRow label="mcc" value={txn.merchantCategoryCode} mono />
              <DetailRow label="fraud_type" value={txn.fraudType ? fraudTypeLabels[txn.fraudType] : "—"} />
            </div>
            <div className="mt-3 grid grid-cols-2 gap-x-4 gap-y-2 border-t pt-3 text-sm sm:grid-cols-3">
              <DetailRow label="impossible_travel" value={String(txn.impossibleTravel)} mono />
              <DetailRow label="prev_txn_km" value={String(txn.prevTxnKm)} mono />
              <DetailRow label="is_vpn / is_tor" value={`${txn.isVpn} / ${txn.isTor}`} mono />
              <DetailRow label="z_score_amount" value={txn.zScoreAmount.toFixed(2)} mono />
              <DetailRow label="txn_count_1m" value={String(txn.txnCount1m)} mono />
              <DetailRow label="dormancy_break / night_flag" value={`${txn.dormancyBreak} / ${txn.nightFlag}`} mono />
            </div>
          </div>
        </TabsContent>

        <TabsContent value="customer" className="mt-4 space-y-3">
          <DetailRow label="Full Name" value={txn.customerName} />
          <DetailRow label="Account Number" value={txn.accountNumber} mono />
          <DetailRow label="Customer ID" value={txn.customerId} mono />
          <DetailRow label="Home Location" value={txn.location.city} />
          {customer && (
            <>
              <DetailRow label="District / Province" value={`${customer.district} / ${customer.province}`} />
              <DetailRow label="KYC Tier" value={customer.kycTier} mono />
              <DetailRow label="Risk Tier" value={customer.riskLevel.toUpperCase()} />
              <DetailRow label="Is Dormant" value={String(customer.isDormant)} mono />
              <DetailRow
                label="Beneficiaries Registered"
                value={String(customer.numBeneficiariesRegistered)}
                mono
              />
            </>
          )}
          <DetailRow label="Registered Device" value={txn.device} />
          <DetailRow label="Session IP" value={txn.ipAddress} mono />
          <DetailRow label="Remarks" value={txn.remarks} />
        </TabsContent>

        <TabsContent value="fraud" className="mt-4 space-y-4">
          {txn.fraud ? (
            <>
              <div>
                <h3 className="mb-2 text-sm font-semibold">Risk Model Assessment</h3>
                <AgentCards agents={txn.fraud.agents} synthesis={txn.fraud.synthesis} />
              </div>
              <div>
                <h3 className="mb-2 text-sm font-semibold">Synthesis</h3>
                <SynthesisGauge synthesis={txn.fraud.synthesis} />
              </div>
              <ShapChart shap={txn.fraud.shap} />
              <NetworkGraph txn={txn} />
            </>
          ) : (
            <EmptyAnalysis />
          )}
        </TabsContent>

        <TabsContent value="timeline" className="mt-4">
          <Timeline txn={txn} />
        </TabsContent>

        <TabsContent value="audit" className="mt-4">
          {verdictLoading ? (
            <div className="flex h-32 items-center justify-center">
              <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
            </div>
          ) : verdict ? (
            <pre className="overflow-x-auto rounded-lg border bg-muted/40 p-4 font-mono text-[11px] leading-relaxed text-muted-foreground">
              {JSON.stringify(verdict, null, 2)}
            </pre>
          ) : (
            <p className="rounded-lg border border-dashed p-6 text-center text-sm text-muted-foreground">
              No synthesis audit record exists for this transaction.
            </p>
          )}
        </TabsContent>
      </Tabs>
    </div>
  );
}

function EmptyAnalysis() {
  return (
    <div className="flex flex-col items-center rounded-lg border border-dashed p-10 text-center">
      <ShieldOff className="mb-3 h-8 w-8 text-muted-foreground" />
      <p className="text-sm font-medium">No AI analysis recorded</p>
      <p className="mt-1 max-w-sm text-xs text-muted-foreground">
        This transaction predates the live fraud pipeline, so no agent verdicts
        were captured for it.
      </p>
    </div>
  );
}

function toChannel(channel: Transaction["channel"]): "MOBILE_APP" | "WEB" | "ATM" | "BRANCH" | "API" {
  switch (channel) {
    case "mobile":
    case "qr":
      return "MOBILE_APP";
    case "web":
      return "WEB";
    case "atm":
      return "ATM";
    case "branch":
      return "BRANCH";
    default:
      return "API";
  }
}

function InfoCard({
  icon: Icon,
  label,
  value,
  sub,
}: {
  icon: typeof User;
  label: string;
  value: string;
  sub?: string;
}) {
  return (
    <div className="rounded-lg border p-3">
      <div className="flex items-center gap-1.5 text-[11px] uppercase tracking-wider text-muted-foreground">
        <Icon className="h-3.5 w-3.5" /> {label}
      </div>
      <div className="mt-1 truncate text-sm font-medium">{value}</div>
      {sub && (
        <div className="truncate font-mono text-[11px] text-muted-foreground">
          {sub}
        </div>
      )}
    </div>
  );
}

function MiniStat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border bg-card p-3 text-center">
      <div className="text-lg font-semibold tabular-nums">{value}</div>
      <div className="text-[10px] uppercase tracking-wider text-muted-foreground">
        {label}
      </div>
    </div>
  );
}

function DetailRow({
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
      <span className={cn("font-medium", mono && "font-mono text-xs")}>
        {value}
      </span>
    </div>
  );
}

const AGENT_TIMELINE_META: Record<string, { icon: typeof Clock; label: string }> = {
  velocity: { icon: Clock, label: "Velocity agent evaluated" },
  geo: { icon: MapPin, label: "Geo agent evaluated" },
  behavior: { icon: CheckCircle2, label: "Behaviour model scored" },
  graph: { icon: CheckCircle2, label: "Graph agent evaluated" },
};

function Timeline({ txn }: { txn: Transaction }) {
  const steps: { icon: typeof Globe; label: string; detail: string; ms: number }[] = [
    { icon: Globe, label: "Transaction initiated", detail: `${txn.channel.toUpperCase()} · ${txn.location.city}`, ms: 0 },
    { icon: Fingerprint, label: "Identity & device check", detail: txn.device, ms: 40 },
  ];
  let elapsed = 40;
  for (const agent of txn.fraud?.agents ?? []) {
    const meta = AGENT_TIMELINE_META[agent.agent] ?? {
      icon: CheckCircle2,
      label: `${agent.agent} agent evaluated`,
    };
    elapsed += Math.max(Math.round(agent.inferenceMs), 10);
    steps.push({
      icon: meta.icon,
      label: meta.label,
      detail: `Score ${agent.risk.toFixed(2)}`,
      ms: elapsed,
    });
  }
  steps.push({
    icon: CheckCircle2,
    label: `Decision: ${txn.decision}`,
    detail: `Final ${txn.riskScore.toFixed(2)}`,
    ms: Math.max(Math.round(txn.latencyMs), elapsed),
  });
  return (
    <div className="relative space-y-1 pl-2">
      {steps.map((s, i) => (
        <div key={i} className="flex gap-3">
          <div className="flex flex-col items-center">
            <div className="flex h-8 w-8 items-center justify-center rounded-full bg-primary/10 text-primary">
              <s.icon className="h-4 w-4" />
            </div>
            {i < steps.length - 1 && <div className="h-6 w-px bg-border" />}
          </div>
          <div className="pb-2">
            <div className="text-sm font-medium">{s.label}</div>
            <div className="text-xs text-muted-foreground">
              {s.detail} · +{s.ms}ms
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}
