"use client";

import { useSyncExternalStore } from "react";
import type { AuthUser } from "@/types/banking";

export interface DemoProfile {
  id: string;
  customerId: string;
  accountId: string;
  name: string;
  label: string;
  blurb: string;
  expected: "PASS" | "OTP" | "BLOCK";
  prefill: {
    fromAccountId: string;
    destination: string;
    recipientAccount: string;
    recipientName: string;
    recipientBank: string;
    amount: number;
    remarks: string;
  };
}

export interface AuthSession {
  token: string;
  user: AuthUser;
  /** Present for demo-profile logins; carries the prefill transaction. */
  profile?: DemoProfile;
}

const STORAGE_KEY = "gime-auth-session";
let current: AuthSession | null = null;
let initialized = false;
const listeners = new Set<() => void>();

function ensureInit() {
  if (initialized || typeof window === "undefined") return;
  initialized = true;
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    current = raw ? (JSON.parse(raw) as AuthSession) : null;
  } catch {
    current = null;
  }
}

function emit() {
  listeners.forEach((l) => l());
}

export function setAuthSession(session: AuthSession | null) {
  current = session;
  if (typeof window !== "undefined") {
    if (session) localStorage.setItem(STORAGE_KEY, JSON.stringify(session));
    else localStorage.removeItem(STORAGE_KEY);
  }
  emit();
}

/** Bearer token for services/http.ts (null when logged out). */
export function getAuthToken(): string | null {
  ensureInit();
  return current?.token ?? null;
}

function subscribe(listener: () => void) {
  ensureInit();
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function getSnapshot(): AuthSession | null {
  ensureInit();
  return current;
}

export function useAuth() {
  const session = useSyncExternalStore(subscribe, getSnapshot, () => null);
  return {
    user: session?.user ?? null,
    token: session?.token ?? null,
    profile: session?.profile ?? null,
    setSession: setAuthSession,
    isAuthenticated: !!session,
  };
}
