"use client";

import { useRouter } from "next/navigation";
import { ArrowLeft, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { TxnDetailView } from "@/features/admin/txn-detail-view";
import { useAdminTransaction } from "@/hooks/useAdmin";

export function AdminTxnDetailPage({ txnId }: { txnId: string }) {
  const router = useRouter();
  const { data: txn, isLoading } = useAdminTransaction(txnId);

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-3">
        <Button variant="ghost" size="sm" onClick={() => router.back()}>
          <ArrowLeft className="h-4 w-4" /> Back
        </Button>
        <div>
          <h1 className="text-xl font-semibold tracking-tight">Transaction Detail</h1>
          <p className="text-sm text-muted-foreground">
            Full fraud analysis and audit trail.
          </p>
        </div>
      </div>

      {isLoading ? (
        <div className="flex h-64 items-center justify-center">
          <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
        </div>
      ) : txn ? (
        <TxnDetailView txn={txn} />
      ) : (
        <p className="py-12 text-center text-sm text-muted-foreground">
          Transaction not found.
        </p>
      )}
    </div>
  );
}
