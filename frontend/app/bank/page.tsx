import { AppHeader } from "@/components/app-header";
import { AuthGuard } from "@/components/auth-guard";
import { BankClient } from "@/components/bank-client";
import { UserMenu } from "@/components/user-menu";

export default function BankPage() {
  return (
    <AuthGuard>
      <div className="min-h-[100dvh]">
        <AppHeader nav right={<UserMenu />} />
        <main className="mx-auto max-w-5xl px-4 py-8 sm:px-6 sm:py-10">
          <BankClient />
        </main>
      </div>
    </AuthGuard>
  );
}
