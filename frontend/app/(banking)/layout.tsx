import { UserShell } from "@/components/user/user-shell";

export default function BankingLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return <UserShell>{children}</UserShell>;
}
