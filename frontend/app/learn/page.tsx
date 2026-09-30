import { AppHeader } from "@/components/app-header";
import { AuthGuard } from "@/components/auth-guard";
import { LearnClient } from "@/components/learn-client";
import { UserMenu } from "@/components/user-menu";

/**
 * 学习推荐页（FR-20）。`?interview=<id>` 由报告页「查看全部推荐」带上——
 * 默认选中的是**来源场次**，不是最近一场（判定见 lib/learn.ts）。
 */
export default async function LearnPage({
  searchParams,
}: {
  searchParams: Promise<{ interview?: string }>;
}) {
  const { interview } = await searchParams;
  return (
    <AuthGuard>
      <div className="min-h-[100dvh]">
        <AppHeader nav right={<UserMenu />} />
        <main className="mx-auto max-w-5xl px-4 py-8 sm:px-6 sm:py-10">
          <LearnClient requestedInterviewId={interview ?? null} />
        </main>
      </div>
    </AuthGuard>
  );
}
