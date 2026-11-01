import { AuthGuard } from "@/components/auth-guard";
import { InterviewClient } from "@/components/interview-client";

export default async function InterviewPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  return (
    <AuthGuard>
      <InterviewClient key={id} interviewId={id} />
    </AuthGuard>
  );
}
