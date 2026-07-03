import type {
  Account,
  Customer,
  DashboardStats,
  RiskLocation,
  SystemService,
  Transaction,
  TrendPoint,
} from "@/types/banking";
import { db } from "@/mock/db";
import { mockRequest } from "./http";

function isToday(iso: string): boolean {
  const d = new Date(iso);
  const now = new Date();
  return d.toDateString() === now.toDateString();
}

/** GET /admin/stats */
export function getDashboardStats(): Promise<DashboardStats> {
  return mockRequest(() => {
    const txns = db.transactions;
    const recent = txns.slice(0, 220); // treat most-recent slice as "today"
    const todayVolume = recent.reduce((sum, t) => sum + t.amount, 0);
    const blocked = txns.filter((t) => t.decision === "BLOCK");
    const otp = txns.filter((t) => t.decision === "OTP");
    const fraudPrevented = blocked.reduce((sum, t) => sum + t.amount, 0);
    void isToday;
    return {
      todayCount: recent.length,
      todayVolume,
      fraudPrevented,
      otpChallenges: otp.length,
      blockedCount: blocked.length,
      activeCustomers: db.customers.filter((c) => c.riskLevel !== "high").length,
      uptime: 99.98,
      avgDetectionMs: 412,
    };
  });
}

/** GET /admin/trends */
export function getTrends(): Promise<TrendPoint[]> {
  return mockRequest(() => {
    const days = 14;
    const points: TrendPoint[] = [];
    for (let i = days - 1; i >= 0; i--) {
      const day = new Date(Date.now() - i * 86400000);
      const dayTxns = db.transactions.filter(
        (t) => new Date(t.timestamp).toDateString() === day.toDateString(),
      );
      points.push({
        label: day.toLocaleDateString("en-GB", {
          day: "2-digit",
          month: "short",
        }),
        transactions: dayTxns.length,
        fraud: dayTxns.filter((t) => t.decision !== "PASS").length,
        volume: Math.round(dayTxns.reduce((s, t) => s + t.amount, 0)),
      });
    }
    return points;
  });
}

/** GET /admin/risk-locations */
export function getRiskLocations(): Promise<RiskLocation[]> {
  return mockRequest(() => {
    const map = new Map<string, { count: number; risk: number }>();
    db.transactions.forEach((t) => {
      const key = t.location.city;
      const entry = map.get(key) ?? { count: 0, risk: 0 };
      entry.count += 1;
      entry.risk += t.riskScore;
      map.set(key, entry);
    });
    return Array.from(map.entries())
      .map(([city, v]) => ({
        city,
        count: v.count,
        avgRisk: Math.round((v.risk / v.count) * 100) / 100,
      }))
      .sort((a, b) => b.avgRisk - a.avgRisk)
      .slice(0, 8);
  });
}

/** GET /admin/live-transactions */
export function getLiveTransactions(limit = 60): Promise<Transaction[]> {
  return mockRequest(() => db.transactions.slice(0, limit), {
    min: 300,
    max: 900,
  });
}

/** GET /admin/flagged */
export function getFlaggedTransactions(): Promise<Transaction[]> {
  return mockRequest(() =>
    db.transactions.filter((t) => t.decision !== "PASS").slice(0, 80),
  );
}

/** GET /admin/customers */
export function getAllCustomers(): Promise<Customer[]> {
  return mockRequest(() => db.customers);
}

/** GET /admin/accounts */
export function getAllAccounts(): Promise<Account[]> {
  return mockRequest(() => db.accounts);
}

/** GET /admin/system-health */
export function getSystemHealth(): Promise<SystemService[]> {
  return mockRequest(() => {
    const base: Omit<SystemService, "status" | "uptime" | "latencyMs">[] = [
      { name: "API Gateway", key: "gateway", category: "gateway" },
      { name: "Velocity Agent", key: "velocity", category: "agent" },
      { name: "Geo Agent", key: "geo", category: "agent" },
      { name: "Behavior Agent", key: "behavior", category: "agent" },
      { name: "Synthesis Agent", key: "synthesis", category: "agent" },
      { name: "Decision / OTP Service", key: "decision", category: "core" },
      { name: "PostgreSQL", key: "postgres", category: "datastore" },
      { name: "Redis", key: "redis", category: "datastore" },
      { name: "Neo4j", key: "neo4j", category: "datastore" },
    ];
    return base.map((s, i) => ({
      ...s,
      status:
        i === 2 ? "degraded" : ("operational" as SystemService["status"]),
      uptime: i === 2 ? 99.4 : 99.9 + Math.random() * 0.09,
      latencyMs: Math.floor(20 + Math.random() * (s.category === "agent" ? 120 : 40)),
    }));
  });
}

/** GET /admin/otp-sessions */
export function getOtpSessions(): Promise<Transaction[]> {
  return mockRequest(() =>
    db.transactions
      .filter((t) => t.decision === "OTP")
      .slice(0, 40),
  );
}
