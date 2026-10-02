import { AuthGuard } from "@/components/auth-guard";
import { BankClient } from "@/components/bank-client";
import { PageShell } from "@/components/page-shell";

export default function BankPage() {
  return (
    <AuthGuard>
      <PageShell>
        <BankClient />
      </PageShell>
    </AuthGuard>
  );
}
