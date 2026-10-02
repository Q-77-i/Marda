import { AuthGuard } from "@/components/auth-guard";
import { PageShell } from "@/components/page-shell";
import { PrivateBankClient } from "@/components/private-bank-client";

export default function PrivateBankPage() {
  return (
    <AuthGuard>
      <PageShell>
        <PrivateBankClient />
      </PageShell>
    </AuthGuard>
  );
}
