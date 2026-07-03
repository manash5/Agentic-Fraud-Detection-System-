import { AdminTxnDetailPage } from "@/features/admin/admin-txn-detail-page";

export default async function AdminTransactionDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  return <AdminTxnDetailPage txnId={decodeURIComponent(id)} />;
}
