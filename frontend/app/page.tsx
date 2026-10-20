import { ChartNoAxesCombined } from "lucide-react";
import Link from "next/link";

import { AuthGuard } from "@/components/auth-guard";
import { DashboardClient } from "@/components/dashboard-client";
import { PageHeader } from "@/components/page-header";
import { PageShell } from "@/components/page-shell";
import { buttonVariants } from "@/components/ui/button";
import { cn } from "@/lib/utils";

export default function DashboardPage() {
  return (
    <AuthGuard>
      <PageShell>
        <PageHeader
          kicker="Workbench"
          title="仪表盘"
          description="继续上次的面试，或开始一场新的训练。"
          right={
            <Link
              href="/profile"
              className={cn(buttonVariants({ variant: "outline" }))}
            >
              <ChartNoAxesCombined className="size-4" strokeWidth={1.8} />
              能力档案
            </Link>
          }
        />
        <DashboardClient />
      </PageShell>
    </AuthGuard>
  );
}
