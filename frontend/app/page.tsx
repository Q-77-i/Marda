import { AppHeader } from "@/components/app-header";
import { CreateForm } from "@/components/create-form";
import { InterviewList } from "@/components/interview-list";

export default function DashboardPage() {
  return (
    <div className="min-h-[100dvh]">
      <AppHeader />
      <main className="mx-auto max-w-5xl px-4 py-8 sm:px-6 sm:py-10">
        <div className="grid gap-6 lg:grid-cols-[minmax(0,340px)_minmax(0,1fr)] lg:items-start">
          <CreateForm />
          <InterviewList />
        </div>
      </main>
    </div>
  );
}
