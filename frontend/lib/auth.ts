"use client";

import { useSyncExternalStore } from "react";
import type { AuthUser } from "@/types/banking";

const STORAGE_KEY = "gime-auth-user";
let current: AuthUser | null = null;
let initialized = false;
const listeners = new Set<() => void>();

function ensureInit() {
  if (initialized || typeof window === "undefined") return;
  initialized = true;
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    current = raw ? (JSON.parse(raw) as AuthUser) : null;
  } catch {
    current = null;
  }
}

function emit() {
  listeners.forEach((l) => l());
}

export function setAuthUser(user: AuthUser | null) {
  current = user;
  if (typeof window !== "undefined") {
    if (user) localStorage.setItem(STORAGE_KEY, JSON.stringify(user));
    else localStorage.removeItem(STORAGE_KEY);
  }
  emit();
}

function subscribe(listener: () => void) {
  ensureInit();
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function getSnapshot(): AuthUser | null {
  ensureInit();
  return current;
}

export function useAuth() {
  const user = useSyncExternalStore(subscribe, getSnapshot, () => null);
  return { user, setUser: setAuthUser, isAuthenticated: !!user };
}
