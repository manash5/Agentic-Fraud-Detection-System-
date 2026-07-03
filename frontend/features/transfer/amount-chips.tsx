"use client";

import { formatNPR } from "@/lib/format";

const DEFAULT_VALUES = [100, 500, 1000, 5000];

export function AmountChips({
  values = DEFAULT_VALUES,
  onSelect,
}: {
  values?: number[];
  onSelect: (value: number) => void;
}) {
  return (
    <div className="flex flex-wrap gap-2 pt-1">
      {values.map((v) => (
        <button
          key={v}
          type="button"
          onClick={() => onSelect(v)}
          className="rounded-full border bg-muted/40 px-3 py-1 text-xs font-medium transition-colors hover:bg-muted"
        >
          {formatNPR(v, false)}
        </button>
      ))}
    </div>
  );
}
