import type { Decision, FraudPattern, TransactionStatus } from "@/types/banking";

export const decisionMeta: Record<
  Decision,
  { label: string; variant: "success" | "warning" | "destructive"; dot: string }
> = {
  PASS: { label: "Pass", variant: "success", dot: "bg-success" },
  OTP: { label: "OTP", variant: "warning", dot: "bg-warning" },
  BLOCK: { label: "Block", variant: "destructive", dot: "bg-destructive" },
};

export const statusMeta: Record<
  TransactionStatus,
  { label: string; variant: "success" | "warning" | "destructive" | "secondary" }
> = {
  success: { label: "Successful", variant: "success" },
  pending: { label: "Pending", variant: "secondary" },
  otp_required: { label: "OTP Required", variant: "warning" },
  failed: { label: "Failed", variant: "destructive" },
  blocked: { label: "Blocked", variant: "destructive" },
};

export const patternLabels: Record<FraudPattern, string> = {
  none: "No pattern",
  rapid_transfers: "Rapid Transfers",
  fraud_ring: "Fraud Ring",
  money_laundering: "Money Laundering",
  account_takeover: "Account Takeover",
  novel_pattern: "Novel Pattern",
};

export function riskColor(score: number): string {
  if (score > 0.7) return "text-destructive";
  if (score >= 0.3) return "text-warning";
  return "text-success";
}

export function riskBg(score: number): string {
  if (score > 0.7) return "bg-destructive";
  if (score >= 0.3) return "bg-warning";
  return "bg-success";
}

export const typeLabels: Record<string, string> = {
  transfer: "Transfer",
  payment: "Payment",
  deposit: "Deposit",
  withdrawal: "Withdrawal",
  qr_payment: "QR Payment",
  topup: "Wallet Top-up",
};
