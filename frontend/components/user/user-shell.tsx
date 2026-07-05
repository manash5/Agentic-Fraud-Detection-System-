"use client";

import * as React from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { motion } from "framer-motion";
import {
  Bell,
  CreditCard,
  Eye,
  EyeOff,
  Grid3x3,
  Landmark,
  LayoutGrid,
  LogOut,
  Search,
  Settings,
  ShieldCheck,
  User,
} from "lucide-react";
import { toast } from "sonner";
import { Brand } from "@/components/shared/brand";
import { MobileActionBar } from "@/components/user/mobile-action-bar";
import { ThemeToggle } from "@/components/theme-toggle";
import {
  Avatar,
  AvatarFallback,
} from "@/components/ui/avatar";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { useAuth } from "@/lib/auth";
import { logout as logoutRequest } from "@/services/authService";
import { useBalanceVisibility } from "@/lib/balance-visibility";
import { initials } from "@/lib/format";
import { cn } from "@/lib/utils";

const nav = [
  { href: "/dashboard", label: "Home", icon: LayoutGrid },
  { href: "/accounts", label: "Accounts", icon: Landmark },
  { href: "/cards", label: "Cards", icon: CreditCard },
  { href: "/hub", label: "Hub", icon: Grid3x3 },
];

export function UserShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const { user } = useAuth();
  const { isVisible, toggle } = useBalanceVisibility();
  const [ready, setReady] = React.useState(false);

  React.useEffect(() => {
    const raw =
      typeof window !== "undefined"
        ? localStorage.getItem("gime-auth-session")
        : null;
    if (!raw) router.replace("/login");
    // eslint-disable-next-line react-hooks/set-state-in-effect
    else setReady(true);
  }, [router]);

  const logout = async () => {
    await logoutRequest().catch(() => undefined);
    router.replace("/login");
  };

  const isHome = pathname === "/dashboard";
  const notify = () => toast.info("No new notifications");
  const search = () => toast.info("Search coming soon");

  if (!ready) return null;

  return (
    <div className="min-h-screen bg-muted/30">
      {/* Desktop sidebar */}
      <aside className="fixed inset-y-0 left-0 hidden w-64 flex-col border-r bg-card lg:flex">
        <div className="flex h-16 items-center border-b px-6">
          <Brand />
        </div>
        <nav className="flex-1 space-y-1 p-4">
          {nav.map((item) => {
            const active =
              pathname === item.href || pathname.startsWith(item.href + "/");
            return (
              <Link
                key={item.href}
                href={item.href}
                className={cn(
                  "flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm font-medium transition-colors",
                  active
                    ? "bg-primary/10 text-primary"
                    : "text-muted-foreground hover:bg-accent hover:text-foreground",
                )}
              >
                <item.icon className="h-[18px] w-[18px]" />
                {item.label}
              </Link>
            );
          })}
        </nav>
        <div className="border-t p-4">
          <Link
            href="/settings"
            className="flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm font-medium text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
          >
            <Settings className="h-[18px] w-[18px]" />
            Settings
          </Link>
          <div className="mt-2 flex items-center gap-2 rounded-lg bg-muted/50 p-2 text-xs text-muted-foreground">
            <ShieldCheck className="h-4 w-4 text-success" />
            Protected by AI fraud shield
          </div>
        </div>
      </aside>

      {/* Main */}
      <div className="lg:pl-64">
        {/* Top bar */}
        <header className="sticky top-0 z-30 flex h-16 items-center justify-between border-b bg-card/80 px-4 backdrop-blur sm:px-6">
          <div className="flex items-center gap-3 lg:hidden">
            <Avatar className="h-9 w-9 border">
              <AvatarFallback>{initials(user?.name ?? "U")}</AvatarFallback>
            </Avatar>
            <div className="text-sm">
              <div className="text-xs text-muted-foreground">{greeting()}</div>
              <div className="font-semibold">{user?.name.split(" ")[0]}!</div>
            </div>
          </div>
          <div className="hidden text-sm text-muted-foreground lg:block">
            {greeting()},{" "}
            <span className="font-medium text-foreground">
              {user?.name.split(" ")[0]}
            </span>
          </div>
          <div className="flex items-center gap-1">
            <button
              onClick={search}
              className="flex h-9 w-9 items-center justify-center rounded-full text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
            >
              <Search className="h-[18px] w-[18px]" />
            </button>
            <button
              onClick={notify}
              className="flex h-9 w-9 items-center justify-center rounded-full text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
            >
              <Bell className="h-[18px] w-[18px]" />
            </button>
            <button
              onClick={toggle}
              className="flex h-9 w-9 items-center justify-center rounded-full text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
            >
              {isVisible ? (
                <Eye className="h-[18px] w-[18px]" />
              ) : (
                <EyeOff className="h-[18px] w-[18px]" />
              )}
            </button>
            <div className="hidden lg:block">
              <ThemeToggle />
            </div>
            <DropdownMenu>
              <DropdownMenuTrigger className="ml-1 outline-none">
                <Avatar className="hidden h-9 w-9 border lg:flex">
                  <AvatarFallback>{initials(user?.name ?? "U")}</AvatarFallback>
                </Avatar>
                <Settings className="h-[18px] w-[18px] text-muted-foreground lg:hidden" />
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end" className="w-56">
                <DropdownMenuLabel>
                  <div className="font-medium">{user?.name}</div>
                  <div className="font-mono text-xs text-muted-foreground">
                    {user?.accountNumber}
                  </div>
                </DropdownMenuLabel>
                <DropdownMenuSeparator />
                <DropdownMenuItem onClick={() => router.push("/profile")}>
                  <User className="h-4 w-4" /> Profile
                </DropdownMenuItem>
                <DropdownMenuItem onClick={() => router.push("/settings")}>
                  <Settings className="h-4 w-4" /> Settings
                </DropdownMenuItem>
                <DropdownMenuSeparator />
                <DropdownMenuItem
                  onClick={logout}
                  className="text-destructive focus:text-destructive"
                >
                  <LogOut className="h-4 w-4" /> Sign out
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </div>
        </header>

        <motion.main
          initial={{ opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.3 }}
          className={cn(
            "mx-auto max-w-5xl px-4 pt-6 sm:px-6",
            isHome ? "pb-36 lg:pb-10" : "pb-24 lg:pb-10",
          )}
        >
          {children}
        </motion.main>
      </div>

      {/* Mobile bottom bar: action bar (Home only) + nav, stacked as one fixed group */}
      <div className="fixed inset-x-0 bottom-0 z-30 lg:hidden">
        {isHome && <MobileActionBar />}
        <nav className="flex items-center justify-around border-t bg-card/95 px-2 py-1.5 backdrop-blur">
          {nav.map((item) => {
            const active =
              pathname === item.href || pathname.startsWith(item.href + "/");
            return (
              <Link
                key={item.href}
                href={item.href}
                className={cn(
                  "flex flex-col items-center gap-0.5 rounded-lg px-3 py-1.5 text-[10px] font-medium transition-colors",
                  active ? "text-primary" : "text-muted-foreground",
                )}
              >
                <item.icon className="h-5 w-5" />
                {item.label}
              </Link>
            );
          })}
        </nav>
      </div>
    </div>
  );
}

function greeting() {
  const h = new Date().getHours();
  if (h < 12) return "Good morning";
  if (h < 17) return "Good afternoon";
  return "Good evening";
}
