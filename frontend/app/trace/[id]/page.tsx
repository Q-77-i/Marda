import { AuthGuard } from "@/components/auth-guard";
import { TraceClient } from "@/components/trace-client";

export default async function TracePage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  return (
    <AuthGuard>
      <TraceClient interviewId={id} />
    </AuthGuard>
  );
}
