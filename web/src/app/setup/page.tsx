"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { ArrowRight, LoaderCircle, LockKeyhole, Mail, ShieldCheck } from "lucide-react";
import { toast } from "sonner";

import { AuthField, AuthShell } from "@/components/auth/auth-card";
import { Button } from "@/components/ui/button";
import { fetchSetupStatus, setupAdmin } from "@/lib/api";
import { getDefaultRouteForRole, setStoredAuthSession } from "@/store/auth";

export default function SetupPage() {
  const router = useRouter();
  const [isChecking, setIsChecking] = useState(true);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");

  useEffect(() => {
    let active = true;
    const check = async () => {
      try {
        const status = await fetchSetupStatus();
        if (!active) return;
        if (!status.requires_setup) {
          router.replace("/login");
          return;
        }
      } catch (error) {
        toast.error(error instanceof Error ? error.message : "读取安装状态失败");
      } finally {
        if (active) setIsChecking(false);
      }
    };
    void check();
    return () => {
      active = false;
    };
  }, [router]);

  const handleSubmit = async () => {
    const normalizedEmail = email.trim().toLowerCase();
    if (!normalizedEmail || !password) {
      toast.error("请输入管理员邮箱和密码");
      return;
    }
    setIsSubmitting(true);
    try {
      const data = await setupAdmin({ email: normalizedEmail, password });
      await setStoredAuthSession({
        key: data.token,
        role: data.role,
        subjectId: data.subject_id,
        name: data.name,
      });
      router.replace(getDefaultRouteForRole(data.role));
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "创建管理员失败");
    } finally {
      setIsSubmitting(false);
    }
  };

  if (isChecking) {
    return (
      <AuthShell title="检查首次安装状态" subtitle="正在确认系统是否已经创建管理员账号。" icon={ShieldCheck} tone="emerald">
        <div className="flex items-center justify-center gap-3 rounded-2xl border border-slate-200/70 bg-white/70 px-4 py-6 text-sm font-medium text-slate-500">
          <LoaderCircle className="size-5 animate-spin text-emerald-500" />
          正在读取安装状态
        </div>
      </AuthShell>
    );
  }

  return (
    <AuthShell
      title="创建首个管理员"
      subtitle="系统检测到还没有管理员账号。完成后旧密钥登录将不再作为主登录入口。"
      icon={ShieldCheck}
      tone="emerald"
      maxWidth="max-w-[560px]"
      footer={
        <div className="text-center">
          已经初始化？
          <Link href="/login" className="ml-1 font-bold text-emerald-700 transition hover:text-emerald-900">
            返回登录
          </Link>
        </div>
      }
    >
      <form
        className="space-y-4"
        onSubmit={(event) => {
          event.preventDefault();
          void handleSubmit();
        }}
      >
        <div className="rounded-2xl border border-emerald-100/90 bg-emerald-50/60 p-3 text-sm leading-6 text-emerald-900">
          该账号会获得管理员权限，用于管理账号池、用户、兑换码、优惠码和系统设置。
        </div>

        <AuthField
          id="setup-email"
          label="管理员邮箱"
          icon={Mail}
          type="email"
          value={email}
          onChange={(event) => setEmail(event.target.value)}
          placeholder="admin@example.com"
          autoComplete="email"
          autoFocus
          disabled={isSubmitting}
          tone="emerald"
        />
        <AuthField
          id="setup-password"
          label="管理员密码"
          icon={LockKeyhole}
          type="password"
          value={password}
          onChange={(event) => setPassword(event.target.value)}
          placeholder="至少 8 位"
          autoComplete="new-password"
          disabled={isSubmitting}
          tone="emerald"
        />

        <Button
          type="submit"
          className="h-12 w-full rounded-2xl bg-gradient-to-r from-emerald-700 via-teal-700 to-emerald-900 font-bold text-white shadow-[0_20px_46px_-24px_rgba(16,185,129,0.95)] hover:from-emerald-800 hover:via-teal-800 hover:to-emerald-950"
          disabled={isSubmitting}
        >
          {isSubmitting ? <LoaderCircle className="size-4 animate-spin" /> : <ArrowRight className="size-4" />}
          创建并进入后台
        </Button>
      </form>
    </AuthShell>
  );
}
