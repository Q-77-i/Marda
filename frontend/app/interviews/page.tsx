import { AuthGuard } from "@/components/auth-guard";
import { InterviewList } from "@/components/interview-list";
import { PageHeader } from "@/components/page-header";
import { PageShell } from "@/components/page-shell";

/**
 * 面试记录页：仪表盘「全部场次」的落点。
 *
 * 列表与仪表盘是同一个组件（不加 limit 即全量）；页头由本页提供，
 * 故关掉卡片头，避免「面试记录」标题重复。
 */
export default function InterviewsPage() {
  return (
    <AuthGuard>
      <PageShell>
        <PageHeader
          kicker="Sessions / Interview records"
          title="面试记录"
          description="全部场次；进行中的可以随时续面，已完成的进入报告。"
        />
        <InterviewList showHeader={false} />
      </PageShell>
    </AuthGuard>
  );
}
