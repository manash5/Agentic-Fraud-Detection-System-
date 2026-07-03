"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import {
  CreditCard,
  Droplet,
  FileText,
  Globe,
  Landmark,
  PiggyBank,
  Phone,
  Router,
  Search,
  Send,
  Shield,
  Tv,
  TrendingUp,
  type LucideIcon,
} from "lucide-react";
import { toast } from "sonner";
import { BrandLogo, type BrandLogoId } from "@/components/shared/brand-logo";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";

interface Tile {
  label: string;
  icon?: LucideIcon;
  logo?: BrandLogoId;
  badge?: string;
  onClick: () => void;
}

export function HubView() {
  const router = useRouter();
  const [query, setQuery] = React.useState("");

  const homeServices: Tile[] = [
    {
      label: "Fund Transfer",
      icon: Send,
      onClick: () => router.push("/transfer/bank"),
    },
    {
      label: "Load Wallet",
      logo: "esewa",
      onClick: () => router.push("/transfer/wallet"),
    },
    {
      label: "Fixed Deposit",
      icon: PiggyBank,
      onClick: () => toast.info("Fixed Deposit application coming soon"),
    },
    {
      label: "Digital Universe",
      icon: Globe,
      onClick: () => toast.info("Digital Universe coming soon"),
    },
    {
      label: "Statement",
      icon: FileText,
      onClick: () => router.push("/history"),
    },
  ];

  const recentServices: Tile[] = [
    {
      label: "Wallet Load",
      logo: "khalti",
      onClick: () => router.push("/transfer/wallet"),
    },
    {
      label: "Apply IPO",
      icon: TrendingUp,
      badge: "New",
      onClick: () => toast.info("IPO application coming soon"),
    },
    {
      label: "Mobile Top-up",
      logo: "ntc",
      onClick: () => router.push("/transfer/topup"),
    },
    {
      label: "E-Com Card",
      icon: CreditCard,
      onClick: () => toast.info("Virtual e-commerce card coming soon"),
    },
  ];

  const billPayments: Tile[] = [
    {
      label: "Mobile Top-up",
      logo: "ncell",
      onClick: () => router.push("/transfer/topup"),
    },
    {
      label: "Data Pack",
      icon: Router,
      onClick: () => router.push("/transfer/bank?mode=bill"),
    },
    {
      label: "Khanepani / NEA",
      icon: Droplet,
      onClick: () => router.push("/transfer/bank?mode=bill"),
    },
    {
      label: "Internet",
      icon: Router,
      onClick: () => router.push("/transfer/bank?mode=bill"),
    },
    {
      label: "Landline",
      icon: Phone,
      onClick: () => router.push("/transfer/bank?mode=bill"),
    },
    {
      label: "TV",
      icon: Tv,
      onClick: () => router.push("/transfer/bank?mode=bill"),
    },
    {
      label: "Insurance",
      icon: Shield,
      onClick: () => router.push("/transfer/bank?mode=bill"),
    },
    {
      label: "Credit Card",
      icon: Landmark,
      onClick: () => router.push("/transfer/bank?mode=bill"),
    },
  ];

  const filter = (tiles: Tile[]) =>
    query.trim()
      ? tiles.filter((t) => t.label.toLowerCase().includes(query.toLowerCase()))
      : tiles;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">Hub</h1>
        <p className="text-sm text-muted-foreground">
          All banking services in one place.
        </p>
      </div>

      <div className="relative">
        <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
        <Input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search services"
          className="pl-9"
        />
      </div>

      <HubSection title="Home Services" tiles={filter(homeServices)} />
      <HubSection title="Recent Services" tiles={filter(recentServices)} />
      <HubSection title="Bill Payments" tiles={filter(billPayments)} />
    </div>
  );
}

function HubSection({ title, tiles }: { title: string; tiles: Tile[] }) {
  if (!tiles.length) return null;
  return (
    <div>
      <h2 className="mb-3 text-sm font-semibold text-muted-foreground">
        {title}
      </h2>
      <div className="grid grid-cols-4 gap-3 sm:grid-cols-5 lg:grid-cols-6">
        {tiles.map((tile) => (
          <button
            key={`${title}-${tile.label}`}
            onClick={tile.onClick}
            className="group relative flex flex-col items-center gap-2 rounded-xl border bg-card p-3 text-center transition-all hover:border-primary/30 hover:shadow-sm"
          >
            {tile.badge && (
              <Badge className="absolute -right-1.5 -top-1.5 px-1.5 py-0 text-[9px]">
                {tile.badge}
              </Badge>
            )}
            <div className="flex h-11 w-11 items-center justify-center overflow-hidden rounded-full bg-primary/10 transition-colors group-hover:bg-primary/5">
              {tile.logo ? (
                <BrandLogo id={tile.logo} size={36} />
              ) : tile.icon ? (
                <tile.icon className="h-5 w-5 text-primary" />
              ) : null}
            </div>
            <span className="text-[11px] font-medium leading-tight">
              {tile.label}
            </span>
          </button>
        ))}
      </div>
    </div>
  );
}
