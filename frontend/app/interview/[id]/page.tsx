import { InterviewClient } from "@/components/interview-client";

export default async function InterviewPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  return <InterviewClient interviewId={id} />;
}
