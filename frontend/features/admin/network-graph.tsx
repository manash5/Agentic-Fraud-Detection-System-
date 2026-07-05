"use client";

// Account network rendered from the live Neo4j graph: the transaction
// account's real SENT-neighborhood, plus the COMM-042 ring when the
// counterparty is the watchlist collector.
import { useQuery } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { getNetworkGraph } from "@/services/adminService";
import type { Transaction } from "@/types/banking";

interface Node {
  id: string;
  label: string;
  x: number;
  y: number;
  type: "self" | "peer" | "fraud" | "recipient";
}

const nodeColor: Record<Node["type"], string> = {
  self: "var(--chart-2)",
  recipient: "var(--chart-3)",
  peer: "var(--muted-foreground)",
  fraud: "var(--destructive)",
};

interface RingData {
  collector: string;
  members: { id: string; transfers: number; total: number }[];
  neighbors: {
    id: string;
    direction: "in" | "out";
    transfers: number;
    is_fraud_seed: boolean;
  }[];
}

function buildRingGraph(
  selfLabel: string,
  ring: RingData,
): { nodes: Node[]; edges: [string, string][] } {
  const members = ring.members.slice(0, 8);
  const nodes: Node[] = [
    { id: "self", label: selfLabel, x: 50, y: 82, type: "self" },
    { id: ring.collector, label: ring.collector, x: 50, y: 15, type: "fraud" },
    ...members.map((m, i) => {
      const angle = (i / members.length) * Math.PI * 2;
      return {
        id: m.id,
        label: m.id,
        x: 50 + Math.cos(angle) * 34,
        y: 48 + Math.sin(angle) * 30,
        type: "peer" as const,
      };
    }),
  ];
  const edges: [string, string][] = [
    ["self", ring.collector],
    ...members.map((m) => [m.id, ring.collector] as [string, string]),
  ];
  return { nodes, edges };
}

function buildNeighborGraph(
  txn: Transaction,
  ring: RingData,
): { nodes: Node[]; edges: [string, string][] } {
  const nodes: Node[] = [
    { id: "self", label: txn.customerName.split(" ")[0], x: 50, y: 50, type: "self" },
    {
      id: "recipient",
      label: txn.counterparty.name.split(" ")[0],
      x: 84,
      y: 40,
      type: txn.riskScore > 0.7 ? "fraud" : "recipient",
    },
  ];
  const edges: [string, string][] = [["self", "recipient"]];
  const neighbors = ring.neighbors.filter((n) => n.id !== txn.counterpartyId);
  neighbors.slice(0, 8).forEach((n, i) => {
    const angle = (i / Math.max(neighbors.length, 1)) * Math.PI * 2 + 0.5;
    nodes.push({
      id: n.id,
      label: n.id,
      x: 50 + Math.cos(angle) * 32,
      y: 50 + Math.sin(angle) * 32,
      type: n.is_fraud_seed ? "fraud" : "peer",
    });
    edges.push(n.direction === "out" ? ["self", n.id] : [n.id, "self"]);
  });
  return { nodes, edges };
}

export function NetworkGraph({ txn }: { txn: Transaction }) {
  const { data: ring, isLoading } = useQuery({
    queryKey: ["admin", "network-graph", txn.accountId],
    queryFn: () => getNetworkGraph(txn.accountId) as Promise<RingData>,
    staleTime: 60_000,
  });

  if (isLoading) {
    return (
      <Card>
        <CardContent className="flex h-48 items-center justify-center p-5">
          <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
        </CardContent>
      </Card>
    );
  }
  if (!ring) return null;

  const isRingCounterparty = txn.counterpartyId === ring.collector;
  const { nodes, edges } = isRingCounterparty
    ? buildRingGraph(txn.customerName.split(" ")[0], ring)
    : buildNeighborGraph(txn, ring);
  const map = new Map(nodes.map((n) => [n.id, n]));

  return (
    <Card>
      <CardContent className="p-5">
        <div className="flex items-center justify-between">
          <div>
            <h3 className="text-sm font-semibold">Account Network</h3>
            <p className="text-xs text-muted-foreground">
              {isRingCounterparty
                ? "Fraud ring COMM-042 (live from Neo4j)"
                : "SENT-edge neighborhood (live from Neo4j)"}
            </p>
          </div>
          {(isRingCounterparty || txn.riskScore > 0.7) && (
            <span className="rounded-md bg-destructive/12 px-2 py-1 text-[11px] font-medium text-destructive">
              {isRingCounterparty
                ? `Fraud-ring seed node ${ring.collector}`
                : "High-risk counterparty"}
            </span>
          )}
        </div>

        <div className="mt-3 aspect-[2/1] w-full overflow-hidden rounded-lg border bg-muted/30">
          <svg viewBox="0 0 100 100" className="h-full w-full" preserveAspectRatio="none">
            {edges.map(([a, b], i) => {
              const na = map.get(a);
              const nb = map.get(b);
              if (!na || !nb) return null;
              const fraudEdge = na.type === "fraud" || nb.type === "fraud";
              return (
                <line
                  key={i}
                  x1={na.x}
                  y1={na.y}
                  x2={nb.x}
                  y2={nb.y}
                  stroke={fraudEdge ? "var(--destructive)" : "var(--border)"}
                  strokeWidth={fraudEdge ? 0.6 : 0.4}
                  strokeDasharray={fraudEdge ? "1.5 1" : undefined}
                  vectorEffect="non-scaling-stroke"
                />
              );
            })}
            {nodes.map((n) => (
              <g key={n.id}>
                <circle
                  cx={n.x}
                  cy={n.y}
                  r={n.type === "self" ? 3.2 : n.type === "fraud" ? 3.6 : 2.4}
                  fill={nodeColor[n.type]}
                  stroke="var(--card)"
                  strokeWidth={0.6}
                  vectorEffect="non-scaling-stroke"
                />
              </g>
            ))}
          </svg>
        </div>

        {isRingCounterparty && (
          <div className="mt-3 grid grid-cols-2 gap-2 text-[11px] text-muted-foreground sm:grid-cols-4">
            <span>
              Ring members:{" "}
              <span className="font-mono text-foreground">{ring.members.length}</span>
            </span>
            <span>
              Collector transfers:{" "}
              <span className="font-mono text-foreground">
                {ring.members.reduce((s, m) => s + m.transfers, 0)}
              </span>
            </span>
            <span>
              Hop distance: <span className="font-mono text-foreground">1</span>
            </span>
            <span>
              is_fraud_seed: <span className="font-mono text-foreground">true</span>
            </span>
          </div>
        )}

        <div className="mt-3 flex flex-wrap gap-3 text-[11px] text-muted-foreground">
          <Legend color={nodeColor.self} label="This account" />
          <Legend color={nodeColor.recipient} label="Recipient" />
          <Legend color={nodeColor.peer} label="Linked account" />
          <Legend color={nodeColor.fraud} label="Flagged node" />
        </div>
      </CardContent>
    </Card>
  );
}

function Legend({ color, label }: { color: string; label: string }) {
  return (
    <span className="flex items-center gap-1.5">
      <span className="h-2.5 w-2.5 rounded-full" style={{ background: color }} />
      {label}
    </span>
  );
}
