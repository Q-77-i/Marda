import { Upload } from "lucide-react";
import Link from "next/link";

import { AuthGuard } from "@/components/auth-guard";
import { BankClient } from "@/components/bank-client";
import { PageHeader } from "@/components/page-header";
import { PageShell } from "@/components/page-shell";
import { buttonVariants } from "@/components/ui/button";
import { cn } from "@/lib/utils";

export default function BankPage() {
  return (
    <AuthGuard>
      <PageShell>
        <PageHeader
          kicker="Library / Question bank"
          title="题库"
          description="按知识域、难度、厂商与面试轮次筛选，或直接检索。每题都带来源与许可。"
          right={
            <Link
              href="/bank/private"
              className={cn(buttonVariants({ variant: "outline" }))}
            >
              <Upload className="size-4" strokeWidth={1.8} />
              上传私有题
            </Link>
          }
        />
        <BankClient />
      </PageShell>
    </AuthGuard>
  );
}
