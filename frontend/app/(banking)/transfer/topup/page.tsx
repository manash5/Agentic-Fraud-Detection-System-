import { Suspense } from "react";
import { MobileTopupFlow } from "@/features/transfer/mobile-topup-flow";

export default function MobileTopupPage() {
  return (
    <Suspense fallback={null}>
      <MobileTopupFlow />
    </Suspense>
  );
}
