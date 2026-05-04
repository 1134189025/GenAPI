"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import {
  ArrowRight,
  Gift,
  KeyRound,
  LoaderCircle,
  LockKeyhole,
  Mail,
  MailCheck,
  RefreshCw,
  Ticket,
  UserPlus,
  WifiOff,
} from "lucide-react";
import { toast } from "sonner";

import { AuthField, AuthNotice, AuthShell } from "@/components/auth/auth-card";
import { resolvePromoQueryParam } from "@/components/auth/promo-query";
import { Button } from "@/components/ui/button";
import { fetchPublicSettings, registerUser, sendVerifyCode, type PublicSettings } from "@/lib/api";
import { useRedirectIfAuthenticated } from "@/lib/use-auth-guard";
import { getDefaultRouteForRole, setStoredAuthSession } from "@/store/auth";

export default function RegisterPage() {
  return (
    <Suspense fallback={<RegisterLoadingState />}>
      <RegisterContent />
    </Suspense>
  );
}

function RegisterLoadingState() {
  return (
    <AuthShell title="准备注册入口" icon={MailCheck}>
      <div className="flex items-center justify-center gap-3 rounded-2xl border border-slate-200/70 bg-white/70 px-4 py-6 text-sm font-medium text-slate-500">
        <LoaderCircle className="size-5 animate-spin text-teal-500" />
        正在加载注册策略
      </div>
    </AuthShell>
  );
}

function RegisterContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const { isCheckingAuth } = useRedirectIfAuthenticated();
  const [settings, setSettings] = useState<PublicSettings | null>(null);
  const [isLoadingSettings, setIsLoadingSettings] = useState(true);
  const [settingsError, setSettingsError] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [isSendingCode, setIsSendingCode] = useState(false);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [verificationCode, setVerificationCode] = useState("");
  const [invitationCode, setInvitationCode] = useState("");
  const [promoCode, setPromoCode] = useState("");

  useEffect(() => {
    const queryPromoCode = resolvePromoQueryParam(searchParams);
    if (!queryPromoCode) {
      return;
    }
    setPromoCode((current) => current || queryPromoCode);
  }, [searchParams]);

  useEffect(() => {
    let active = true;
    const load = async () => {
      try {
        setSettingsError("");
        const data = await fetchPublicSettings();
        if (active) {
          setSettings(data.settings);
        }
      } catch (error) {
        if (active) {
          setSettings(null);
          setSettingsError(error instanceof Error ? error.message : "读取注册设置失败");
        }
      } finally {
        if (active) {
          setIsLoadingSettings(false);
        }
      }
    };
    void load();
    return () => {
      active = false;
    };
  }, []);

  const normalizedEmail = email.trim().toLowerCase();
  const emailVerificationEnabled = Boolean(settings?.email_verification_enabled);

  const handleReloadSettings = async () => {
    setIsLoadingSettings(true);
    setSettingsError("");
    try {
      const data = await fetchPublicSettings();
      setSettings(data.settings);
    } catch (error) {
      setSettings(null);
      setSettingsError(error instanceof Error ? error.message : "读取注册设置失败");
    } finally {
      setIsLoadingSettings(false);
    }
  };

  const handleSendCode = async () => {
    if (!normalizedEmail) {
      toast.error("请先输入邮箱");
      return;
    }
    setIsSendingCode(true);
    try {
      await sendVerifyCode(normalizedEmail);
      toast.success("验证码已发送");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "发送验证码失败");
    } finally {
      setIsSendingCode(false);
    }
  };

  const handleRegister = async () => {
    if (!normalizedEmail || !password) {
      toast.error("请输入邮箱和密码");
      return;
    }
    if (emailVerificationEnabled && !verificationCode.trim()) {
      toast.error("请输入邮箱验证码");
      return;
    }
    if (settings?.invitation_required && !invitationCode.trim()) {
      toast.error("请输入邀请码");
      return;
    }

    setIsSubmitting(true);
    try {
      const data = await registerUser({
        email: normalizedEmail,
        password,
        verification_code: verificationCode.trim(),
        invitation_code: invitationCode.trim(),
        promo_code: promoCode.trim(),
      });
      await setStoredAuthSession({
        key: data.token,
        role: data.role,
        subjectId: data.subject_id,
        name: data.name,
      });
      router.replace(getDefaultRouteForRole(data.role));
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "注册失败");
    } finally {
      setIsSubmitting(false);
    }
  };

  if (isCheckingAuth || isLoadingSettings) {
    return <RegisterLoadingState />;
  }

  if (settings && !settings.registration_enabled) {
    const brandName = settings.site_name || "Genapi";
    return (
      <AuthShell title="注册暂未开放" brandName={brandName} icon={Ticket} tone="amber">
        <AuthNotice
          title="公开注册入口已关闭"
          description="管理员可以在系统设置中重新开放注册，或为你手动创建用户账号。"
          icon={Ticket}
          tone="amber"
        >
          <Button asChild className="h-11 rounded-2xl bg-slate-950 px-5 font-bold text-white hover:bg-slate-800">
            <Link href="/login">返回登录</Link>
          </Button>
        </AuthNotice>
      </AuthShell>
    );
  }

  if (!settings) {
    return (
      <AuthShell
        title="无法读取注册设置"
        icon={WifiOff}
        tone="amber"
      >
        <AuthNotice
          title="注册设置加载失败"
          description={settingsError || "请确认后端服务可访问后重试。"}
          icon={WifiOff}
          tone="amber"
        >
          <Button
            type="button"
            className="h-11 rounded-2xl bg-amber-600 px-5 font-bold text-white hover:bg-amber-700"
            onClick={() => void handleReloadSettings()}
          >
            <RefreshCw className="size-4" />
            重新加载
          </Button>
          <Button asChild variant="outline" className="h-11 rounded-2xl border-slate-200 bg-white/85 px-5 font-bold">
            <Link href="/login">返回登录</Link>
          </Button>
        </AuthNotice>
      </AuthShell>
    );
  }

  const brandName = settings.site_name || "Genapi";
  const emailHint =
    settings.email_domain_whitelist.length > 0
      ? `允许域名：${settings.email_domain_whitelist.join("、")}`
      : undefined;

  return (
    <AuthShell
      title="创建用户账号"
      subtitle="图片请求会按账号额度扣减。"
      brandName={brandName}
      icon={UserPlus}
      maxWidth="max-w-[590px]"
      footer={
        <div className="text-center">
          已有账号？
          <Link href="/login" className="ml-1 font-bold text-teal-700 transition hover:text-teal-900">
            返回登录
          </Link>
        </div>
      }
    >
      <form
        className="space-y-4"
        onSubmit={(event) => {
          event.preventDefault();
          void handleRegister();
        }}
      >
        <div className="grid gap-2 rounded-2xl border border-teal-100/80 bg-teal-50/60 p-3 text-xs font-bold text-slate-600 sm:grid-cols-3">
          <span>{emailVerificationEnabled ? "邮箱验证已启用" : "邮箱验证未启用"}</span>
          <span>{settings.invitation_required ? "邀请码必填" : "无需邀请码"}</span>
          <span>{settings.promo_codes_enabled ? "可填写优惠码" : "优惠码未启用"}</span>
        </div>

        <AuthField
          id="register-email"
          label="邮箱"
          icon={Mail}
          type="email"
          value={email}
          onChange={(event) => setEmail(event.target.value)}
          placeholder="name@example.com"
          autoComplete="email"
          autoFocus
          disabled={isSubmitting}
          hint={emailHint}
        />

        <AuthField
          id="register-password"
          label="密码"
          icon={LockKeyhole}
          type="password"
          value={password}
          onChange={(event) => setPassword(event.target.value)}
          placeholder="至少 8 位"
          autoComplete="new-password"
          disabled={isSubmitting}
        />

        {emailVerificationEnabled ? (
          <div className="grid gap-3 sm:grid-cols-[1fr_auto] sm:items-end">
            <AuthField
              id="register-verification-code"
              label="邮箱验证码"
              icon={MailCheck}
              value={verificationCode}
              onChange={(event) => setVerificationCode(event.target.value)}
              placeholder="6 位验证码"
              autoComplete="one-time-code"
              disabled={isSubmitting}
              wrapperClassName="min-w-0"
            />
            <Button
              type="button"
              variant="outline"
              className="h-12 rounded-2xl border-slate-200 bg-white/85 px-5 font-bold text-slate-700 hover:bg-teal-50 hover:text-teal-800"
              disabled={isSendingCode || isSubmitting}
              onClick={() => void handleSendCode()}
            >
              {isSendingCode ? <LoaderCircle className="size-4 animate-spin" /> : <MailCheck className="size-4" />}
              发送验证码
            </Button>
          </div>
        ) : null}

        {settings.invitation_required ? (
          <AuthField
            id="register-invitation-code"
            label="邀请码"
            icon={KeyRound}
            value={invitationCode}
            onChange={(event) => setInvitationCode(event.target.value)}
            placeholder="INV-..."
            disabled={isSubmitting}
          />
        ) : null}

        {settings.promo_codes_enabled ? (
          <AuthField
            id="register-promo-code"
            label="优惠码（可选）"
            icon={Gift}
            value={promoCode}
            onChange={(event) => setPromoCode(event.target.value)}
            placeholder="WELCOME"
            disabled={isSubmitting}
            hint={promoCode ? "已从链接或输入框填入，提交注册时会一并校验。" : undefined}
          />
        ) : null}

        <Button
          type="submit"
          className="h-12 w-full rounded-2xl bg-gradient-to-r from-teal-600 via-cyan-600 to-sky-600 font-bold text-white shadow-[0_20px_46px_-24px_rgba(14,165,233,0.95)] hover:from-teal-700 hover:via-cyan-700 hover:to-sky-700"
          disabled={isSubmitting}
        >
          {isSubmitting ? <LoaderCircle className="size-4 animate-spin" /> : <ArrowRight className="size-4" />}
          注册并登录
        </Button>
      </form>
    </AuthShell>
  );
}
