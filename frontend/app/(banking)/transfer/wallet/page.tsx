import { Suspense } from "react";
import { WalletLoadFlow } from "@/features/transfer/wallet-load-flow";

export default function WalletTransferPage() {
  return (
    <Suspense fallback={null}>
      <WalletLoadFlow />
    </Suspense>
  );
}
