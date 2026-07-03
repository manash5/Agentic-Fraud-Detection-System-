"use client";

import * as React from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  Activity,
  BarChart3,
  Building2,
  FileBarChart,
  LayoutDashboard,
  MonitorCheck,
  Search,
  ShieldAlert,
  Users,
  KeyRound,
  Settings,
  Bell,
} from "lucide-react";
import { ThemeToggle } from "@/components/theme-toggle";
import { BrandLogo } from "@/components/shared/brand-logo";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";

const nav = [
  { href: "/admin", label: "Dashboard", icon: LayoutDashboard },
  { href: "/admin/transactions", label: "Transactions", icon: Activity },
  { href: "/admin/fraud", label: "Fraud Monitoring", icon: ShieldAlert },
  { href: "/admin/customers", label: "Customers", icon: Users },
  { href: "/admin/accounts", label: "Accounts", icon: Building2 },
  { href: "/admin/otp", label: "OTP Center", icon: KeyRound },
  { href: "/admin/analytics", label: "Analytics", icon: BarChart3 },
  { href: "/admin/reports", label: "Reports", icon: FileBarChart },
  { href: "/admin/system-health", label: "System Health", icon: MonitorCheck },
  { href: "/admin/settings", label: "Settings", icon: Settings },
];

export function AdminShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();

  return (
    <div className="min-h-screen bg-muted/40">
      {/* Sidebar */}
      <aside className="fixed inset-y-0 left-0 z-30 hidden w-60 flex-col bg-sidebar text-sidebar-foreground lg:flex">
        <div className="flex h-16 items-center gap-2.5 border-b border-sidebar-border px-5">
          <BrandLogo id="global-ime" size={32} className="rounded-lg" />
          <div className="leading-tight">
            <div className="text-sm font-semibold text-white">GIBL Admin</div>
            <div className="text-[10px] uppercase tracking-widest text-sidebar-foreground/50">
              Operations Portal
            </div>
          </div>
        </div>
        <nav className="flex-1 space-y-0.5 overflow-y-auto p-3 no-scrollbar">
          {nav.map((item) => {
            const active =
              item.href === "/admin"
                ? pathname === "/admin"
                : pathname.startsWith(item.href);
            return (
              <Link
                key={item.href}
                href={item.href}
                className={cn(
                  "flex items-center gap-3 rounded-md px-3 py-2 text-sm font-medium transition-colors",
                  active
                    ? "bg-sidebar-accent text-white"
                    : "text-sidebar-foreground/70 hover:bg-sidebar-accent/60 hover:text-white",
                )}
              >
                <item.icon className="h-[18px] w-[18px]" />
                {item.label}
              </Link>
            );
          })}
        </nav>
        <div className="border-t border-sidebar-border p-3">
          <div className="flex items-center gap-2 rounded-md bg-sidebar-accent/50 px-3 py-2 text-xs">
            <span className="h-2 w-2 rounded-full bg-success" />
            <span className="text-sidebar-foreground/80">
              All systems operational
            </span>
          </div>
        </div>
      </aside>

      {/* Main */}
      <div className="lg:pl-60">
        <header className="sticky top-0 z-20 flex h-16 items-center justify-between border-b bg-card px-4 sm:px-6">
          <div className="flex items-center gap-3">
            <div className="lg:hidden">
              <BrandLogo id="global-ime" size={28} className="rounded-md" />
            </div>
            <div className="relative hidden sm:block">
              <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
              <Input
                placeholder="Search transaction ID, customer…"
                className="w-72 pl-9"
              />
            </div>
          </div>
          <div className="flex items-center gap-2">
            <Badge variant="success" className="hidden gap-1.5 sm:flex">
              <span className="h-1.5 w-1.5 rounded-full bg-success" />
              Live
            </Badge>
            <button className="relative flex h-9 w-9 items-center justify-center rounded-md text-muted-foreground hover:bg-accent">
              <Bell className="h-5 w-5" />
              <span className="absolute right-2 top-2 h-1.5 w-1.5 rounded-full bg-destructive" />
            </button>
            <ThemeToggle />
            <div className="flex items-center gap-2 pl-1">
              <Avatar className="h-8 w-8">
                <AvatarFallback className="bg-secondary text-secondary-foreground">
                  FA
                </AvatarFallback>
              </Avatar>
              <div className="hidden text-sm leading-tight sm:block">
                <div className="font-medium">Fraud Analyst</div>
                <div className="text-xs text-muted-foreground">
                  Risk Operations
                </div>
              </div>
            </div>
          </div>
        </header>

        <main className="p-4 sm:p-6">{children}</main>
      </div>
    </div>
  );
}
