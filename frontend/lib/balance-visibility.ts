"use client";

import { useSyncExternalStore } from "react";

// Shared "eye toggle" state so the header control and the Easy Balance card
// stay in sync without prop drilling through the shell.
let visible = true;
const listeners = new Set<() => void>();

function emit() {
  listeners.forEach((l) => l());
}

export function setBalanceVisible(next: boolean) {
  visible = next;
  emit();
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function getSnapshot() {
  return visible;
}

export function useBalanceVisibility() {
  const isVisible = useSyncExternalStore(subscribe, getSnapshot, () => true);
  return { isVisible, toggle: () => setBalanceVisible(!visible), setVisible: setBalanceVisible };
}
