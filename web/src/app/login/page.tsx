"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { ArrowRight, LoaderCircle, LockKeyhole, Mail, ShieldCheck, UserPlus, Wrench } from "lucide-react";
import { toast } from "sonner";

import { AuthField, AuthShell } from "@/components/auth/auth-card";
import { Button } from "@/components/ui/button";
import { login } from "@/lib/api";
import { useRedirectIfAuthenticated } from "@/lib/use-auth-guard";
import { getDefaultRouteForRole, setStoredAuthSession } from "@/store/auth";

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const { isCheckingAuth } = useRedirectIfAuthenticated();

  const handleLogin = async () => {
    const normalizedEmail = email.trim().toLowerCase();
    if (!normalizedEmail || !password) {
      toast.error("请输入邮箱和密码");
      return;
    }

    setIsSubmitting(true);
    try {
      const data = await login(normalizedEmail, password);
      await setStoredAuthSession({
        key: data.token,
        role: data.role,
        subjectId: data.subject_id,
        name: data.name,
      });
      router.replace(getDefaultRouteForRole(data.role));
    } catch (error) {
      const message = error instanceof Error ? error.message : "登录失败";
      toast.error(message);
    } finally {
      setIsSubmitting(false);
    }
  };

  if (isCheckingAuth) {
    return (
      <AuthShell title="正在进入控制台" icon={ShieldCheck}>
        <div className="flex items-center justify-center gap-3 rounded-2xl border border-slate-200/70 bg-white/70 px-4 py-6 text-sm font-medium text-slate-500">
          <LoaderCircle className="size-5 animate-spin text-teal-500" />
          正在检查会话
        </div>
      </AuthShell>
    );
  }

  return (
    <AuthShell
      title="欢迎回来"
      icon={LockKeyhole}
      footer={
        <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
          <Link href="/register" className="inline-flex items-center gap-2 font-bold text-teal-700 transition hover:text-teal-900">
            <UserPlus className="size-4" />
            创建账号
          </Link>
          <Link href="/setup" className="inline-flex items-center gap-2 font-bold text-slate-600 transition hover:text-slate-950">
            <Wrench className="size-4" />
            首次安装
          </Link>
        </div>
      }
    >
      <form
        className="space-y-4"
        onSubmit={(event) => {
          event.preventDefault();
          void handleLogin();
        }}
      >
        <AuthField
          id="login-email"
          label="邮箱"
          icon={Mail}
          type="email"
          value={email}
          onChange={(event) => setEmail(event.target.value)}
          placeholder="name@example.com"
          autoComplete="email"
          autoFocus
          disabled={isSubmitting}
        />
        <AuthField
          id="login-password"
          label="密码"
          icon={LockKeyhole}
          type="password"
          value={password}
          onChange={(event) => setPassword(event.target.value)}
          placeholder="请输入密码"
          autoComplete="current-password"
          disabled={isSubmitting}
        />

        <Button
          type="submit"
          className="h-12 w-full rounded-2xl bg-gradient-to-r from-slate-950 via-teal-900 to-slate-950 font-bold text-white shadow-[0_20px_46px_-24px_rgba(15,118,110,0.95)] hover:from-slate-900 hover:via-teal-800 hover:to-slate-900"
          disabled={isSubmitting}
        >
          {isSubmitting ? <LoaderCircle className="size-4 animate-spin" /> : <ArrowRight className="size-4" />}
          登录
        </Button>
      </form>
    </AuthShell>
  );
}
