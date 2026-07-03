import { ArrowDownRight, ArrowUpRight } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

export function StatCard({
  label,
  value,
  delta,
  icon: Icon,
  loading,
  tone = "default",
}: {
  label: string;
  value: string;
  delta?: { value: string; up: boolean; good?: boolean };
  icon: React.ComponentType<{ className?: string }>;
  loading?: boolean;
  tone?: "default" | "success" | "warning" | "destructive";
}) {
  const toneMap = {
    default: "bg-primary/10 text-primary",
    success: "bg-success/12 text-success",
    warning: "bg-warning/15 text-warning",
    destructive: "bg-destructive/12 text-destructive",
  };

  return (
    <Card>
      <CardContent className="p-5">
        <div className="flex items-center justify-between">
          <span className="text-xs font-medium text-muted-foreground">
            {label}
          </span>
          <div
            className={cn(
              "flex h-8 w-8 items-center justify-center rounded-lg",
              toneMap[tone],
            )}
          >
            <Icon className="h-4 w-4" />
          </div>
        </div>
        {loading ? (
          <Skeleton className="mt-3 h-7 w-24" />
        ) : (
          <div className="mt-2 text-2xl font-semibold tabular-nums">
            {value}
          </div>
        )}
        {delta && !loading && (
          <div
            className={cn(
              "mt-1.5 flex items-center gap-1 text-xs font-medium",
              delta.good ?? delta.up
                ? "text-success"
                : "text-destructive",
            )}
          >
            {delta.up ? (
              <ArrowUpRight className="h-3 w-3" />
            ) : (
              <ArrowDownRight className="h-3 w-3" />
            )}
            {delta.value}
            <span className="text-muted-foreground">vs last week</span>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
