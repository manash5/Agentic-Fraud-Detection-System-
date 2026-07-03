"use client";

import {
  Boxes,
  Database,
  Network,
  Server,
  ShieldCheck,
} from "lucide-react";
import { PageHeader } from "@/components/admin/page-header";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useSystemHealth } from "@/hooks/useAdmin";
import { cn } from "@/lib/utils";
import type { SystemService } from "@/types/banking";

const categoryIcon = {
  gateway: Network,
  agent: ShieldCheck,
  core: Server,
  datastore: Database,
} as const;

const statusMeta = {
  operational: { label: "Operational", dot: "bg-success", text: "text-success" },
  degraded: { label: "Degraded", dot: "bg-warning", text: "text-warning" },
  down: { label: "Down", dot: "bg-destructive", text: "text-destructive" },
} as const;

export default function SystemHealthPage() {
  const { data, isLoading } = useSystemHealth();

  const operational =
    data?.filter((s) => s.status === "operational").length ?? 0;

  return (
    <div>
      <PageHeader
        title="System Health"
        description="Live status of all microservices and data stores."
        action={
          <div className="flex items-center gap-2 rounded-lg border bg-card px-3 py-2 text-sm">
            <Boxes className="h-4 w-4 text-muted-foreground" />
            <span className="font-medium">
              {operational}/{data?.length ?? 9}
            </span>
            <span className="text-muted-foreground">services healthy</span>
          </div>
        }
      />

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {isLoading
          ? Array.from({ length: 9 }).map((_, i) => (
              <Skeleton key={i} className="h-32" />
            ))
          : data?.map((s) => <ServiceCard key={s.key} service={s} />)}
      </div>
    </div>
  );
}

function ServiceCard({ service }: { service: SystemService }) {
  const Icon = categoryIcon[service.category];
  const meta = statusMeta[service.status];
  return (
    <Card>
      <CardContent className="p-5">
        <div className="flex items-start justify-between">
          <div className="flex items-center gap-2.5">
            <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-muted text-muted-foreground">
              <Icon className="h-[18px] w-[18px]" />
            </div>
            <div>
              <div className="text-sm font-semibold">{service.name}</div>
              <div className="text-[11px] capitalize text-muted-foreground">
                {service.category}
              </div>
            </div>
          </div>
          <span
            className={cn("flex items-center gap-1.5 text-xs font-medium", meta.text)}
          >
            <span className={cn("h-2 w-2 rounded-full", meta.dot)} />
            {meta.label}
          </span>
        </div>
        <div className="mt-4 grid grid-cols-2 gap-3">
          <div>
            <div className="text-[10px] uppercase tracking-wider text-muted-foreground">
              Uptime
            </div>
            <div className="text-sm font-semibold tabular-nums">
              {service.uptime.toFixed(2)}%
            </div>
          </div>
          <div>
            <div className="text-[10px] uppercase tracking-wider text-muted-foreground">
              Latency
            </div>
            <div className="text-sm font-semibold tabular-nums">
              {service.latencyMs}ms
            </div>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}
