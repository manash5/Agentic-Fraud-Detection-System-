import { Suspense } from "react";
import { HistoryView } from "@/features/history/history-view";

export default function HistoryPage() {
  return (
    <Suspense fallback={null}>
      <HistoryView />
    </Suspense>
  );
}
