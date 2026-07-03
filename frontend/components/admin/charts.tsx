"use client";

import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { TrendPoint } from "@/types/banking";

const axisStyle = {
  fontSize: 11,
  fill: "var(--muted-foreground)",
};

const tooltipStyle = {
  background: "var(--popover)",
  border: "1px solid var(--border)",
  borderRadius: 8,
  fontSize: 12,
  color: "var(--popover-foreground)",
};

export function TransactionTrendChart({ data }: { data: TrendPoint[] }) {
  return (
    <ResponsiveContainer width="100%" height={260}>
      <AreaChart data={data} margin={{ top: 10, right: 6, left: -18, bottom: 0 }}>
        <defs>
          <linearGradient id="txnFill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="var(--chart-2)" stopOpacity={0.35} />
            <stop offset="100%" stopColor="var(--chart-2)" stopOpacity={0} />
          </linearGradient>
        </defs>
        <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" vertical={false} />
        <XAxis dataKey="label" tick={axisStyle} tickLine={false} axisLine={false} />
        <YAxis tick={axisStyle} tickLine={false} axisLine={false} />
        <Tooltip contentStyle={tooltipStyle} cursor={{ stroke: "var(--border)" }} />
        <Area
          type="monotone"
          dataKey="transactions"
          stroke="var(--chart-2)"
          strokeWidth={2}
          fill="url(#txnFill)"
          name="Transactions"
        />
      </AreaChart>
    </ResponsiveContainer>
  );
}

export function FraudTrendChart({ data }: { data: TrendPoint[] }) {
  return (
    <ResponsiveContainer width="100%" height={260}>
      <LineChart data={data} margin={{ top: 10, right: 6, left: -18, bottom: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" vertical={false} />
        <XAxis dataKey="label" tick={axisStyle} tickLine={false} axisLine={false} />
        <YAxis tick={axisStyle} tickLine={false} axisLine={false} />
        <Tooltip contentStyle={tooltipStyle} cursor={{ stroke: "var(--border)" }} />
        <Line
          type="monotone"
          dataKey="fraud"
          stroke="var(--chart-1)"
          strokeWidth={2}
          dot={false}
          name="Flagged"
        />
      </LineChart>
    </ResponsiveContainer>
  );
}

export function VolumeBarChart({ data }: { data: TrendPoint[] }) {
  return (
    <ResponsiveContainer width="100%" height={220}>
      <BarChart data={data} margin={{ top: 10, right: 6, left: -18, bottom: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" vertical={false} />
        <XAxis dataKey="label" tick={axisStyle} tickLine={false} axisLine={false} />
        <YAxis tick={axisStyle} tickLine={false} axisLine={false} />
        <Tooltip contentStyle={tooltipStyle} cursor={{ fill: "var(--accent)" }} />
        <Bar dataKey="volume" radius={[4, 4, 0, 0]} name="Volume (NPR)">
          {data.map((_, i) => (
            <Cell key={i} fill="var(--chart-3)" />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}

export function DecisionDonut({
  pass,
  otp,
  block,
}: {
  pass: number;
  otp: number;
  block: number;
}) {
  const total = pass + otp + block || 1;
  const segments = [
    { label: "Allow", value: pass, color: "var(--success)" },
    { label: "OTP Only", value: otp, color: "var(--warning)" },
    { label: "Block", value: block, color: "var(--destructive)" },
  ];
  let acc = 0;
  const radius = 54;
  const circ = 2 * Math.PI * radius;

  return (
    <div className="flex items-center gap-6">
      <svg width={140} height={140} viewBox="0 0 140 140">
        <circle
          cx={70}
          cy={70}
          r={radius}
          fill="none"
          stroke="var(--muted)"
          strokeWidth={16}
        />
        {segments.map((s) => {
          const frac = s.value / total;
          const dash = frac * circ;
          const el = (
            <circle
              key={s.label}
              cx={70}
              cy={70}
              r={radius}
              fill="none"
              stroke={s.color}
              strokeWidth={16}
              strokeDasharray={`${dash} ${circ - dash}`}
              strokeDashoffset={-acc * circ}
              transform="rotate(-90 70 70)"
              strokeLinecap="butt"
            />
          );
          acc += frac;
          return el;
        })}
        <text
          x={70}
          y={66}
          textAnchor="middle"
          className="fill-foreground"
          style={{ fontSize: 22, fontWeight: 600 }}
        >
          {total}
        </text>
        <text
          x={70}
          y={84}
          textAnchor="middle"
          className="fill-muted-foreground"
          style={{ fontSize: 10 }}
        >
          transactions
        </text>
      </svg>
      <div className="space-y-2">
        {segments.map((s) => (
          <div key={s.label} className="flex items-center gap-2 text-sm">
            <span
              className="h-2.5 w-2.5 rounded-sm"
              style={{ background: s.color }}
            />
            <span className="text-muted-foreground">{s.label}</span>
            <span className="ml-auto font-medium tabular-nums">{s.value}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
