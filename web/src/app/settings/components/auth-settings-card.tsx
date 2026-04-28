"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { LoaderCircle, Save, Ticket, Users } from "lucide-react";
import { toast } from "sonner";

import { DataPanel } from "@/components/common/data-panel";
import { EmptyState } from "@/components/common/empty-state";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { fetchAuthSettings, updateAuthSettings, type AuthSettings } from "@/lib/api";
import { buildAuthSettingsPayload, normalizeAuthSettingsForForm } from "./auth-settings-helpers";

export function AuthSettingsCard() {
  const didLoadRef = useRef(false);
  const [settings, setSettings] = useState<AuthSettings | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isSaving, setIsSaving] = useState(false);

  useEffect(() => {
    if (didLoadRef.current) return;
    didLoadRef.current = true;
    const load = async () => {
      try {
        const data = await fetchAuthSettings();
        setSettings(normalizeAuthSettingsForForm(data.settings));
      } catch (error) {
        toast.error(error instanceof Error ? error.message : "加载认证设置失败");
      } finally {
        setIsLoading(false);
      }
    };
    void load();
  }, []);

  const patch = (updates: Partial<AuthSettings>) => {
    setSettings((current) => (current ? { ...current, ...updates } : current));
  };

  const handleSave = async () => {
    if (!settings) return;
    setIsSaving(true);
    try {
      const data = await updateAuthSettings(buildAuthSettingsPayload(settings));
      setSettings(normalizeAuthSettingsForForm(data.settings));
      toast.success("认证设置已保存");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "保存认证设置失败");
    } finally {
      setIsSaving(false);
    }
  };

  if (isLoading) {
    return (
      <DataPanel title="注册与认证设置" description="正在读取邮箱验证、邀请码、默认额度和 SMTP 配置。">
        <div className="p-5">
          <EmptyState
            title="正在加载认证设置"
            description="从后端同步注册与 SMTP 配置。"
            icon={<LoaderCircle className="size-7 animate-spin" />}
          />
        </div>
      </DataPanel>
    );
  }

  if (!settings) return null;

  return (
    <DataPanel
      title="注册与认证设置"
      description="配置邮箱验证、邀请码、优惠码、默认图片额度和 SMTP。SMTP 密码留空会保留旧密码。"
      toolbar={
        <>
            <Button asChild variant="outline" className="h-9 rounded-xl border-stone-200 bg-white">
              <Link href="/admin/users">
                <Users className="size-4" />
                用户管理
              </Link>
            </Button>
            <Button asChild variant="outline" className="h-9 rounded-xl border-stone-200 bg-white">
              <Link href="/admin/redeem-codes">
                <Ticket className="size-4" />
                码管理
              </Link>
            </Button>
            <Button className="h-9 rounded-xl bg-stone-950 px-4 text-white hover:bg-stone-800" disabled={isSaving} onClick={() => void handleSave()}>
              {isSaving ? <LoaderCircle className="size-4 animate-spin" /> : <Save className="size-4" />}
              保存
            </Button>
        </>
      }
    >
      <div className="space-y-6 p-6">

        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
          {[
            ["开放注册", "registration_enabled"],
            ["邮箱验证", "email_verification_enabled"],
            ["邀请码必填", "invitation_required"],
            ["启用优惠码", "promo_codes_enabled"],
          ].map(([label, key]) => (
            <label key={key} className="flex items-center justify-between rounded-2xl border border-stone-200 bg-white px-4 py-3 text-sm text-stone-700">
              {label}
              <Checkbox
                checked={Boolean(settings[key as keyof AuthSettings])}
                onCheckedChange={(checked) => patch({ [key]: Boolean(checked) } as Partial<AuthSettings>)}
              />
            </label>
          ))}
        </div>

        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          <div className="space-y-2">
            <label className="text-sm text-stone-700">站点名称</label>
            <Input value={settings.site_name} onChange={(event) => patch({ site_name: event.target.value })} className="h-10 rounded-xl border-stone-200 bg-white" />
          </div>
          <div className="space-y-2">
            <label className="text-sm text-stone-700">默认图片额度</label>
            <Input value={String(settings.default_image_quota)} type="number" min={0} onChange={(event) => patch({ default_image_quota: Number(event.target.value) || 0 })} className="h-10 rounded-xl border-stone-200 bg-white" />
          </div>
          <div className="space-y-2">
            <label className="text-sm text-stone-700">默认图片并发</label>
            <Input value={String(settings.default_image_concurrency)} type="number" min={1} onChange={(event) => patch({ default_image_concurrency: Number(event.target.value) || 1 })} className="h-10 rounded-xl border-stone-200 bg-white" />
          </div>
          <div className="space-y-2">
            <label className="text-sm text-stone-700">验证码冷却秒数</label>
            <Input value={String(settings.verify_send_cooldown_seconds)} type="number" min={0} onChange={(event) => patch({ verify_send_cooldown_seconds: Number(event.target.value) || 60 })} className="h-10 rounded-xl border-stone-200 bg-white" />
          </div>
          <div className="space-y-2">
            <label className="text-sm text-stone-700">验证码有效期（秒）</label>
            <Input value={String(settings.verify_code_ttl_seconds)} type="number" min={0} onChange={(event) => patch({ verify_code_ttl_seconds: Number(event.target.value) || 900 })} className="h-10 rounded-xl border-stone-200 bg-white" />
          </div>
          <div className="space-y-2">
            <label className="text-sm text-stone-700">最大尝试次数</label>
            <Input value={String(settings.verify_max_attempts)} type="number" min={1} onChange={(event) => patch({ verify_max_attempts: Number(event.target.value) || 5 })} className="h-10 rounded-xl border-stone-200 bg-white" />
          </div>
        </div>

        <div className="grid gap-4 md:grid-cols-2">
          <div className="space-y-2">
            <label className="text-sm text-stone-700">邮箱后缀白名单</label>
            <Textarea
              value={settings.email_domain_whitelist.join("\n")}
              onChange={(event) =>
                patch({
                  email_domain_whitelist: event.target.value
                    .split(/[\n,]/)
                    .map((item) => item.trim().toLowerCase())
                    .filter(Boolean),
                })
              }
              placeholder="example.com，每行一个；留空则不限制"
              className="min-h-24 rounded-xl border-stone-200 bg-white font-mono text-xs"
            />
          </div>
          <div className="grid gap-4 md:grid-cols-2">
            <div className="space-y-2">
              <label className="text-sm text-stone-700">SMTP Host</label>
              <Input value={settings.smtp_host} onChange={(event) => patch({ smtp_host: event.target.value })} className="h-10 rounded-xl border-stone-200 bg-white" />
            </div>
            <div className="space-y-2">
              <label className="text-sm text-stone-700">SMTP Port</label>
              <Input value={String(settings.smtp_port)} type="number" min={0} onChange={(event) => patch({ smtp_port: Number(event.target.value) || 587 })} className="h-10 rounded-xl border-stone-200 bg-white" />
            </div>
            <div className="space-y-2">
              <label className="text-sm text-stone-700">SMTP Username</label>
              <Input value={settings.smtp_username} onChange={(event) => patch({ smtp_username: event.target.value })} className="h-10 rounded-xl border-stone-200 bg-white" />
            </div>
            <div className="space-y-2">
              <label className="text-sm text-stone-700">SMTP Password</label>
              <Input
                value={settings.smtp_password}
                type="password"
                onChange={(event) => patch({ smtp_password: event.target.value })}
                placeholder={settings.has_smtp_password ? "已配置，留空保留" : "未配置"}
                className="h-10 rounded-xl border-stone-200 bg-white"
              />
            </div>
            <label className="flex items-center justify-between rounded-xl border border-stone-200 bg-white px-4 py-3 text-sm text-stone-700 md:col-span-2">
              <span>
                SMTP TLS
                <span className="mt-1 block text-xs text-stone-500">开启后使用 STARTTLS；465 端口仍会使用隐式 SSL。</span>
              </span>
              <Checkbox
                checked={Boolean(settings.smtp_tls)}
                onCheckedChange={(checked) => patch({ smtp_tls: Boolean(checked) })}
              />
            </label>
            <div className="space-y-2 md:col-span-2">
              <label className="text-sm text-stone-700">SMTP From</label>
              <Input value={settings.smtp_from} onChange={(event) => patch({ smtp_from: event.target.value })} className="h-10 rounded-xl border-stone-200 bg-white" />
            </div>
          </div>
        </div>
      </div>
    </DataPanel>
  );
}
