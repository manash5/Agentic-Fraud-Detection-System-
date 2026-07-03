import { Suspense } from "react";
import { BankTransferFlow } from "@/features/transfer/bank-transfer-flow";

export default function BankTransferPage() {
  return (
    <Suspense fallback={null}>
      <BankTransferFlow />
    </Suspense>
  );
}
