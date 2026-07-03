"use client";

import { Activity, Brain, GitBranch, MapPin, Sparkles } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { fraudTypeLabels } from "@/lib/trackb";
import { patternLabels, riskColor } from "@/lib/risk";
import { cn } from "@/lib/utils";
import type { AgentResult, FraudAnalysis } from "@/types/banking";

const agentMeta = {
  velocity: { label: "Velocity Agent", icon: Activity, desc: "Speed & frequency" },
  geo: { label: "Geo Agent", icon: MapPin, desc: "Location & network" },
  behavior: { label: "Behavior Agent", icon: Brain, desc: "ML behavioural" },
  graph: { label: "Graph Agent", icon: GitBranch, desc: "Account network / rings" },
};

const flagStyles: Record<string, string> = {
  LOW: "bg-success/10 text-success",
  MEDIUM: "bg-warning/10 text-warning",
  HIGH: "bg-destructive/10 text-destructive",
  CRITICAL: "bg-destructive/20 text-destructive",
};

export function AgentCards({
  agents,
  synthesis,
}: {
  agents: AgentResult[];
  synthesis?: FraudAnalysis["synthesis"];
}) {
  return (
    <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
      {agents.map((a) => {
        const meta = agentMeta[a.agent];
        return (
          <Card key={a.agent}>
            <CardContent className="p-4">
              <div className="flex items-start justify-between gap-2">
                <div className="flex items-center gap-2">
                  <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary/10 text-primary">
                    <meta.icon className="h-4 w-4" />
                  </div>
                  <div>
                    <div className="text-sm font-semibold">{meta.label}</div>
                    <div className="text-[11px] text-muted-foreground">
                      {meta.desc}
                    </div>
                  </div>
                </div>
                <span
                  className={cn(
                    "shrink-0 rounded-md px-1.5 py-0.5 text-[10px] font-semibold uppercase",
                    flagStyles[a.flag],
                  )}
                >
                  {a.flag}
                </span>
              </div>

              <div className="mt-4 grid grid-cols-2 gap-3">
                <Metric label="Score" value={a.risk} colored />
                <Metric label="Confidence" value={a.confidence} />
              </div>
              <div className="mt-2 text-[10px] text-muted-foreground">
                inference_ms: <span className="font-mono">{a.inferenceMs}</span>
              </div>

              <div className="mt-3 space-y-1.5">
                {a.reasons.map((r, i) => (
                  <div
                    key={i}
                    className="flex items-start gap-1.5 text-[11px] text-muted-foreground"
                  >
                    <span className="mt-1 h-1 w-1 shrink-0 rounded-full bg-muted-foreground/50" />
                    {r}
                  </div>
                ))}
              </div>
            </CardContent>
          </Card>
        );
      })}

      {synthesis && (
        <Card className="border-primary/30">
          <CardContent className="p-4">
            <div className="flex items-center gap-2">
              <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary/10 text-primary">
                <Sparkles className="h-4 w-4" />
              </div>
              <div>
                <div className="text-sm font-semibold">Synthesis Agent</div>
                <div className="text-[11px] text-muted-foreground">
                  Ensemble decision
                </div>
              </div>
            </div>

            <div className="mt-4 grid grid-cols-2 gap-3">
              <Metric label="Fraud Prob." value={synthesis.finalRisk} colored />
              <Metric label="Confidence" value={synthesis.confidence} />
            </div>
            <div className="mt-2 text-[10px] text-muted-foreground">
              inference_ms:{" "}
              <span className="font-mono">{synthesis.inferenceMs}</span>
            </div>

            <div className="mt-3 space-y-1.5 text-[11px] text-muted-foreground">
              <div>
                Weights applied:{" "}
                <span className="font-mono text-foreground">
                  {Object.entries(synthesis.weights)
                    .map(([k, v]) => `${k[0]}=${v.toFixed(2)}`)
                    .join(" · ")}
                </span>
              </div>
              <div>
                Pattern:{" "}
                <span className="font-medium text-foreground">
                  {patternLabels[synthesis.pattern]}
                </span>
              </div>
              {synthesis.fraudType && (
                <div>
                  fraud_type:{" "}
                  <span className="font-medium text-foreground">
                    {fraudTypeLabels[synthesis.fraudType]}
                  </span>
                </div>
              )}
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  );
}

function Metric({
  label,
  value,
  colored,
}: {
  label: string;
  value: number;
  colored?: boolean;
}) {
  return (
    <div>
      <div className="mb-1 flex items-center justify-between">
        <span className="text-[10px] uppercase tracking-wider text-muted-foreground">
          {label}
        </span>
        <span
          className={cn(
            "text-xs font-semibold tabular-nums",
            colored && riskColor(value),
          )}
        >
          {value.toFixed(2)}
        </span>
      </div>
      <div className="h-1.5 overflow-hidden rounded-full bg-muted">
        <div
          className={cn(
            "h-full rounded-full",
            colored
              ? value > 0.7
                ? "bg-destructive"
                : value >= 0.3
                  ? "bg-warning"
                  : "bg-success"
              : "bg-chart-2",
          )}
          style={{ width: `${value * 100}%` }}
        />
      </div>
    </div>
  );
}

export function SynthesisGauge({
  synthesis,
}: {
  synthesis: FraudAnalysis["synthesis"];
}) {
  const score = synthesis.finalRisk;
  const angle = -90 + score * 180;
  const color =
    score > 0.7
      ? "var(--destructive)"
      : score >= 0.3
        ? "var(--warning)"
        : "var(--success)";

  return (
    <Card>
      <CardContent className="p-5">
        <div className="grid items-center gap-4 sm:grid-cols-[auto_1fr]">
          <div className="relative mx-auto">
            <svg width={180} height={104} viewBox="0 0 180 104">
              <path
                d="M 16 96 A 74 74 0 0 1 164 96"
                fill="none"
                stroke="var(--muted)"
                strokeWidth={14}
                strokeLinecap="round"
              />
              <path
                d="M 16 96 A 74 74 0 0 1 164 96"
                fill="none"
                stroke={color}
                strokeWidth={14}
                strokeLinecap="round"
                strokeDasharray={`${score * 232} 232`}
              />
              <line
                x1={90}
                y1={96}
                x2={90 + 60 * Math.cos((angle * Math.PI) / 180)}
                y2={96 + 60 * Math.sin((angle * Math.PI) / 180)}
                stroke="var(--foreground)"
                strokeWidth={2.5}
                strokeLinecap="round"
              />
              <circle cx={90} cy={96} r={5} fill="var(--foreground)" />
            </svg>
            <div className="text-center">
              <div
                className="text-2xl font-semibold tabular-nums"
                style={{ color }}
              >
                {score.toFixed(2)}
              </div>
              <div className="text-[10px] uppercase tracking-wider text-muted-foreground">
                Fraud Probability
              </div>
            </div>
          </div>

          <div className="space-y-2.5">
            <GaugeRow
              label="Decision"
              value={synthesis.decision}
              accent={color}
            />
            <GaugeRow
              label="Fraud Pattern"
              value={patternLabels[synthesis.pattern]}
            />
            {synthesis.fraudType && (
              <GaugeRow
                label="fraud_type"
                value={fraudTypeLabels[synthesis.fraudType]}
              />
            )}
            <GaugeRow
              label="Confidence"
              value={`${(synthesis.confidence * 100).toFixed(0)}%`}
            />
            <GaugeRow
              label="Agent Disagreement"
              value={synthesis.disagreement.toFixed(3)}
            />
            <div className="pt-1">
              <div className="mb-1 text-[10px] uppercase tracking-wider text-muted-foreground">
                Applied Weights
              </div>
              <div className="flex gap-2">
                {Object.entries(synthesis.weights).map(([k, v]) => (
                  <div
                    key={k}
                    className="flex-1 rounded-md bg-muted px-2 py-1 text-center"
                  >
                    <div className="text-xs font-semibold">{v.toFixed(2)}</div>
                    <div className="text-[9px] capitalize text-muted-foreground">
                      {k}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

function GaugeRow({
  label,
  value,
  accent,
}: {
  label: string;
  value: string;
  accent?: string;
}) {
  return (
    <div className="flex items-center justify-between text-sm">
      <span className="text-muted-foreground">{label}</span>
      <span className="font-semibold" style={accent ? { color: accent } : {}}>
        {value}
      </span>
    </div>
  );
}

export function ShapChart({ shap }: { shap: FraudAnalysis["shap"] }) {
  const max = Math.max(...shap.map((s) => Math.abs(s.contribution)), 0.01);
  return (
    <Card>
      <CardContent className="p-5">
        <h3 className="text-sm font-semibold">Explainability (SHAP)</h3>
        <p className="mb-4 text-xs text-muted-foreground">
          Feature contributions to the final risk score.
        </p>
        <div className="space-y-2.5">
          {shap.map((f) => {
            const positive = f.contribution >= 0;
            const width = (Math.abs(f.contribution) / max) * 50;
            return (
              <div key={f.feature} className="flex items-center gap-2 text-xs">
                <div className="w-36 truncate text-right font-mono text-muted-foreground">
                  {f.feature}
                </div>
                <div className="relative flex h-5 flex-1 items-center">
                  <div className="absolute left-1/2 h-full w-px bg-border" />
                  <div
                    className={cn(
                      "absolute h-3.5 rounded-sm",
                      positive ? "bg-destructive/80" : "bg-success/80",
                    )}
                    style={{
                      width: `${width}%`,
                      left: positive ? "50%" : `${50 - width}%`,
                    }}
                  />
                </div>
                <div
                  className={cn(
                    "w-12 text-right font-mono tabular-nums",
                    positive ? "text-destructive" : "text-success",
                  )}
                >
                  {f.contribution > 0 ? "+" : ""}
                  {f.contribution.toFixed(2)}
                </div>
              </div>
            );
          })}
        </div>
        <div className="mt-4 flex justify-center gap-6 text-[11px] text-muted-foreground">
          <span className="flex items-center gap-1.5">
            <span className="h-2 w-2 rounded-sm bg-destructive/80" /> Increases
            fraud
          </span>
          <span className="flex items-center gap-1.5">
            <span className="h-2 w-2 rounded-sm bg-success/80" /> Decreases fraud
          </span>
        </div>
      </CardContent>
    </Card>
  );
}
