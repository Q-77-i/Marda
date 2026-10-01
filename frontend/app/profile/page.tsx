import { AppHeader } from "@/components/app-header";
import { AuthGuard } from "@/components/auth-guard";
import { ProfileClient } from "@/components/profile-client";
import { UserMenu } from "@/components/user-menu";

/** 能力档案页（FR-19）：多场得分曲线与短板变化。数据源 = 该用户的全部已落库报告。 */
export default function ProfilePage() {
  return (
    <AuthGuard>
      <div className="min-h-[100dvh]">
        <AppHeader nav right={<UserMenu />} />
        <main className="mx-auto max-w-5xl px-4 py-8 sm:px-6 sm:py-10">
          <ProfileClient />
        </main>
      </div>
    </AuthGuard>
  );
}
