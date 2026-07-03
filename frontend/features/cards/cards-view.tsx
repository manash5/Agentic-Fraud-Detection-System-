"use client";

import * as React from "react";
import { motion } from "framer-motion";
import { Eye, EyeOff, Lock, Snowflake, Wifi } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useCards } from "@/hooks/useBanking";
import { useAuth } from "@/lib/auth";
import { formatNPR } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { Card as BankCard } from "@/types/banking";

export function CardsView() {
  const { user } = useAuth();
  const { data: cards, isLoading } = useCards(user?.customerId);

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">My Cards</h1>
        <p className="text-sm text-muted-foreground">
          Manage your debit and credit cards.
        </p>
      </div>

      {isLoading ? (
        <div className="grid gap-4 sm:grid-cols-2">
          <Skeleton className="h-52" />
          <Skeleton className="h-52" />
        </div>
      ) : (
        <div className="grid gap-6 sm:grid-cols-2">
          {cards?.map((card) => (
            <CardTile key={card.id} card={card} />
          ))}
        </div>
      )}
    </div>
  );
}

function CardTile({ card }: { card: BankCard }) {
  const [reveal, setReveal] = React.useState(false);
  const credit = card.type === "credit";

  return (
    <div className="space-y-3">
      <motion.div
        whileHover={{ y: -4 }}
        className={cn(
          "relative aspect-[1.586/1] overflow-hidden rounded-2xl p-5 text-white shadow-lg",
        )}
        style={{
          background: credit
            ? "linear-gradient(135deg, oklch(0.28 0.02 264), oklch(0.18 0.015 264))"
            : "linear-gradient(135deg, oklch(0.52 0.19 20), oklch(0.4 0.16 15))",
        }}
      >
        <div
          className="pointer-events-none absolute -right-10 -top-10 h-40 w-40 rounded-full opacity-20"
          style={{ background: "radial-gradient(circle, white, transparent 70%)" }}
        />
        <div className="flex items-start justify-between">
          <div>
            <div className="text-[10px] uppercase tracking-widest text-white/60">
              Global IME Bank
            </div>
            <div className="text-xs font-medium text-white/80">
              {credit ? "Credit Card" : "Debit Card"}
            </div>
          </div>
          <Wifi className="h-5 w-5 rotate-90 text-white/70" />
        </div>

        <div className="mt-8 font-mono text-lg tracking-widest">
          {reveal ? card.number : `•••• •••• •••• ${card.number.slice(-4)}`}
        </div>

        <div className="mt-6 flex items-end justify-between">
          <div>
            <div className="text-[9px] uppercase tracking-wider text-white/50">
              Card Holder
            </div>
            <div className="text-sm font-medium">{card.holder}</div>
          </div>
          <div>
            <div className="text-[9px] uppercase tracking-wider text-white/50">
              Expires
            </div>
            <div className="text-sm font-medium">{card.expiry}</div>
          </div>
          <div className="text-sm font-bold uppercase italic tracking-tight">
            {card.scheme}
          </div>
        </div>
      </motion.div>

      <div className="flex items-center justify-between px-1">
        <div className="text-xs text-muted-foreground">
          Limit:{" "}
          <span className="font-medium text-foreground">
            {formatNPR(card.limit, false)}
          </span>
        </div>
        <div className="flex gap-1">
          <Button
            variant="ghost"
            size="sm"
            onClick={() => setReveal((r) => !r)}
          >
            {reveal ? (
              <EyeOff className="h-4 w-4" />
            ) : (
              <Eye className="h-4 w-4" />
            )}
            {reveal ? "Hide" : "Show"}
          </Button>
          <Button
            variant="ghost"
            size="sm"
            onClick={() => toast.success("Card frozen temporarily")}
          >
            <Snowflake className="h-4 w-4" /> Freeze
          </Button>
          <Button
            variant="ghost"
            size="sm"
            onClick={() => toast.info("PIN change flow started")}
          >
            <Lock className="h-4 w-4" /> PIN
          </Button>
        </div>
      </div>
    </div>
  );
}
