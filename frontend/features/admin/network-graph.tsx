"use client";

import { Card, CardContent } from "@/components/ui/card";
import { COMM_042_COLLECTOR, COMM_042_MEMBERS } from "@/mock/trackb-fixtures";
import type { Transaction } from "@/types/banking";

interface Node {
  id: string;
  label: string;
  x: number;
  y: number;
  type: "self" | "peer" | "fraud" | "recipient";
}

// Deterministic mock graph around the transaction — placeholder for Neo4j.
function buildGraph(txn: Transaction): { nodes: Node[]; edges: [string, string][] } {
  const seed = txn.id.split("").reduce((a, c) => a + c.charCodeAt(0), 0);
  const rnd = (n: number) => (Math.sin(seed * (n + 1)) + 1) / 2;

  const nodes: Node[] = [
    { id: "self", label: txn.customerName.split(" ")[0], x: 50, y: 50, type: "self" },
    {
      id: "recipient",
      label: txn.counterparty.name.split(" ")[0],
      x: 82,
      y: 42,
      type: "recipient",
    },
  ];
  const peerCount = 3 + Math.floor(rnd(1) * 3);
  const highRisk = txn.riskScore > 0.55;
  for (let i = 0; i < peerCount; i++) {
    nodes.push({
      id: `peer-${i}`,
      label: `ACC-${1000 + Math.floor(rnd(i + 2) * 8999)}`,
      x: 15 + rnd(i + 3) * 70,
      y: 15 + rnd(i + 5) * 70,
      type: highRisk && i === 0 ? "fraud" : "peer",
    });
  }

  const edges: [string, string][] = [
    ["self", "recipient"],
    ...nodes
      .filter((n) => n.id.startsWith("peer"))
      .map((n) => ["self", n.id] as [string, string]),
  ];
  if (highRisk) edges.push(["recipient", "peer-0"]);
  return { nodes, edges };
}

// COMM-042: this transaction's counterparty is the ring's fraud-seed
// collector account — render the real 7-member ring instead of a
// placeholder graph.
function buildComm042Graph(selfLabel: string): { nodes: Node[]; edges: [string, string][] } {
  const nodes: Node[] = [
    { id: "self", label: selfLabel, x: 50, y: 82, type: "self" },
    { id: "collector", label: COMM_042_COLLECTOR, x: 50, y: 15, type: "fraud" },
    ...COMM_042_MEMBERS.map((id, i) => {
      const angle = (i / COMM_042_MEMBERS.length) * Math.PI * 2;
      return {
        id,
        label: id,
        x: 50 + Math.cos(angle) * 34,
        y: 48 + Math.sin(angle) * 30,
        type: "peer" as const,
      };
    }),
  ];
  const edges: [string, string][] = [
    ["self", "collector"],
    ...COMM_042_MEMBERS.map((id) => [id, "collector"] as [string, string]),
  ];
  return { nodes, edges };
}

const nodeColor: Record<Node["type"], string> = {
  self: "var(--chart-2)",
  recipient: "var(--chart-3)",
  peer: "var(--muted-foreground)",
  fraud: "var(--destructive)",
};

export function NetworkGraph({ txn }: { txn: Transaction }) {
  const isComm042 = txn.counterpartyId === COMM_042_COLLECTOR;
  const { nodes, edges } = isComm042
    ? buildComm042Graph(txn.customerName.split(" ")[0])
    : buildGraph(txn);
  const map = new Map(nodes.map((n) => [n.id, n]));

  return (
    <Card>
      <CardContent className="p-5">
        <div className="flex items-center justify-between">
          <div>
            <h3 className="text-sm font-semibold">Account Network</h3>
            <p className="text-xs text-muted-foreground">
              {isComm042
                ? "Fraud ring COMM-042 (Neo4j integration ready)"
                : "Graph relationships (Neo4j integration ready)"}
            </p>
          </div>
          {(isComm042 || txn.riskScore > 0.55) && (
            <span className="rounded-md bg-destructive/12 px-2 py-1 text-[11px] font-medium text-destructive">
              {isComm042
                ? `Fraud-ring seed node ${COMM_042_COLLECTOR}`
                : "Fraud ring proximity detected"}
            </span>
          )}
        </div>

        <div className="mt-3 aspect-[2/1] w-full overflow-hidden rounded-lg border bg-muted/30">
          <svg viewBox="0 0 100 100" className="h-full w-full" preserveAspectRatio="none">
            {edges.map(([a, b], i) => {
              const na = map.get(a)!;
              const nb = map.get(b)!;
              const fraudEdge =
                na.type === "fraud" || nb.type === "fraud";
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

        {isComm042 && (
          <div className="mt-3 grid grid-cols-2 gap-2 text-[11px] text-muted-foreground sm:grid-cols-4">
            <span>Collector in-degree: <span className="font-mono text-foreground">34 / 7d</span></span>
            <span>Ring members: <span className="font-mono text-foreground">{COMM_042_MEMBERS.length}</span></span>
            <span>Hop distance: <span className="font-mono text-foreground">1</span></span>
            <span>is_fraud_seed: <span className="font-mono text-foreground">true</span></span>
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
