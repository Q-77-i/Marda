import { AuthGuard } from "@/components/auth-guard";
import { PageShell } from "@/components/page-shell";
import { ProfileClient } from "@/components/profile-client";

/** 能力档案页（FR-19）：多场得分曲线与短板变化。数据源 = 该用户的全部已落库报告。 */
export default function ProfilePage() {
  return (
    <AuthGuard>
      <PageShell>
        <ProfileClient />
      </PageShell>
    </AuthGuard>
  );
}
