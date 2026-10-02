import { AuthGuard } from "@/components/auth-guard";
import { CreateForm } from "@/components/create-form";
import { InterviewList } from "@/components/interview-list";
import { PageShell } from "@/components/page-shell";

export default function DashboardPage() {
  return (
    <AuthGuard>
      <PageShell>
        <div className="grid gap-6 lg:grid-cols-[minmax(0,340px)_minmax(0,1fr)] lg:items-start">
          <CreateForm />
          <InterviewList />
        </div>
      </PageShell>
    </AuthGuard>
  );
}
