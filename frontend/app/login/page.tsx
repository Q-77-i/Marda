import { AppHeader } from "@/components/app-header";
import { LoginForm } from "@/components/login-form";

export default function LoginPage() {
  return (
    <div className="flex min-h-[100dvh] flex-col">
      <AppHeader />
      <main className="flex flex-1 items-center justify-center px-4 py-10">
        <LoginForm />
      </main>
    </div>
  );
}
