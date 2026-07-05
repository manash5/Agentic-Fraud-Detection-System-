"use client";

// The live transfer engine shared by the bank / wallet / top-up flows:
// submit the transfer (202), then poll the backend status endpoint while the
// Kafka pipeline runs, exposing agent-by-agent progress, the OTP challenge
// and the terminal outcome.
import * as React from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  getTransferStatus,
  submitTransfer,
  type TransferStatus,
} from "@/services/transferService";
import { upsertLocalTransaction } from "@/lib/txn-local-store";
import type { Transaction, TransferRequest } from "@/types/banking";

const TERMINAL = new Set(["completed", "blocked", "failed"]);
const PIPELINE_TIMEOUT_MS = 90_000;

export interface TransferRun {
  txnId: string | null;
  status: TransferStatus | null;
  /** Set once the run is done via OTP verification (carries the receipt txn). */
  verifiedTxn: Transaction | null;
  timedOut: boolean;
  start: (req: TransferRequest) => Promise<void>;
  markVerified: (txn: Transaction) => void;
  reset: () => void;
}

export function useTransferRun(): TransferRun {
  const queryClient = useQueryClient();
  const [txnId, setTxnId] = React.useState<string | null>(null);
  const [startedAt, setStartedAt] = React.useState<number | null>(null);
  const [verifiedTxn, setVerifiedTxn] = React.useState<Transaction | null>(null);

  const query = useQuery({
    queryKey: ["transfer-status", txnId],
    queryFn: () => getTransferStatus(txnId!),
    enabled: !!txnId && !verifiedTxn,
    refetchInterval: (q) =>
      q.state.data && TERMINAL.has(q.state.data.status) ? false : 1000,
    staleTime: 0,
    retry: 2,
  });

  const status = query.data ?? null;
  const timedOut = Boolean(
    txnId &&
      startedAt &&
      status?.status === "processing" &&
      Date.now() - startedAt > PIPELINE_TIMEOUT_MS,
  );

  // A finished run changes balances + history — refresh them.
  const terminal = status ? TERMINAL.has(status.status) : false;
  React.useEffect(() => {
    if (terminal && status?.txn) upsertLocalTransaction(status.txn);
  }, [terminal, status?.txn]);

  React.useEffect(() => {
    if (verifiedTxn) upsertLocalTransaction(verifiedTxn);
  }, [verifiedTxn]);

  React.useEffect(() => {
    if (terminal || verifiedTxn) {
      queryClient.invalidateQueries({ queryKey: ["accounts"] });
      queryClient.invalidateQueries({ queryKey: ["transactions"] });
      queryClient.invalidateQueries({ queryKey: ["admin"] });
    }
  }, [terminal, verifiedTxn, queryClient]);

  const start = React.useCallback(async (req: TransferRequest) => {
    setVerifiedTxn(null);
    const res = await submitTransfer(req);
    setStartedAt(Date.now());
    setTxnId(res.txnId);
  }, []);

  const markVerified = React.useCallback((txn: Transaction) => {
    setVerifiedTxn(txn);
  }, []);

  const reset = React.useCallback(() => {
    setTxnId(null);
    setStartedAt(null);
    setVerifiedTxn(null);
  }, []);

  return { txnId, status, verifiedTxn, timedOut, start, markVerified, reset };
}
