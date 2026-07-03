"use client";

import { useRouter } from "next/navigation";
import { Landmark } from "lucide-react";
import { BrandLogo, type BrandLogoId } from "@/components/shared/brand-logo";
import { FlowHeader } from "./flow-header";

const options: {
  label: string;
  desc: string;
  href: string;
  logo?: BrandLogoId;
  icon?: typeof Landmark;
}[] = [
  {
    label: "Bank Transfer",
    desc: "Own account, Global IME, other banks & bill payments",
    icon: Landmark,
    href: "/transfer/bank",
  },
  {
    label: "Wallet Load",
    desc: "Load money into your eSewa or Khalti wallet",
    logo: "esewa",
    href: "/transfer/wallet",
  },
  {
    label: "Mobile Top-up",
    desc: "Recharge NTC or Ncell prepaid balance",
    logo: "ntc",
    href: "/transfer/topup",
  },
];

export function TransferHub() {
  const router = useRouter();

  return (
    <div className="mx-auto max-w-lg">
      <FlowHeader title="Send Money" onBack={() => router.push("/dashboard")} />
      <div className="space-y-3">
        {options.map((o) => (
          <button
            key={o.label}
            onClick={() => router.push(o.href)}
            className="flex w-full items-center gap-4 rounded-xl border bg-card p-4 text-left transition-all hover:border-primary/30 hover:shadow-sm"
          >
            <div className="flex h-11 w-11 shrink-0 items-center justify-center overflow-hidden rounded-full bg-primary/10">
              {o.logo ? (
                <BrandLogo id={o.logo} size={40} className="rounded-full" />
              ) : o.icon ? (
                <o.icon className="h-5 w-5 text-primary" />
              ) : null}
            </div>
            <div>
              <div className="text-sm font-semibold">{o.label}</div>
              <div className="text-xs text-muted-foreground">{o.desc}</div>
            </div>
          </button>
        ))}
      </div>
    </div>
  );
}
