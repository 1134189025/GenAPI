"use client";

import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";
import { CalendarClock, Crown, Gift, LoaderCircle, RefreshCw, Sparkles } from "lucide-react";
import { toast } from "sonner";

import { DataPanel } from "@/components/common/data-panel";
import { EmptyState } from "@/components/common/empty-state";
import { PageHeader } from "@/components/common/page-header";
import { StatCard } from "@/components/common/stat-card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { fetchMembershipPlans, fetchUserMembership, type ManagedUser, type MembershipPlan, type UserMembership } from "@/lib/api";
import { useAuthGuard } from "@/lib/use-auth-guard";

function formatDate(value: string | null | undefined) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

function statusLabel(status: string | undefined) {
  if (status === "active") return "有效会员";
  if (status === "expired") return "已过期";
  return "未开通";
}

export default function MembershipPage() {
  const { isCheckingAuth, session } = useAuthGuard();
  const didLoadRef = useRef(false);
  const [user, setUser] = useState<ManagedUser | null>(null);
  const [membership, setMembership] = useState<UserMembership | null>(null);
  const [plans, setPlans] = useState<MembershipPlan[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const canLoadMembershipData = !isCheckingAuth && Boolean(session);

  const load = async () => {
    setIsLoading(true);
    try {
      const [membershipData, planData] = await Promise.all([fetchUserMembership(), fetchMembershipPlans()]);
      setUser(membershipData.user);
      setMembership(membershipData.membership);
      setPlans(planData.items);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "加载会员信息失败");
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    if (!canLoadMembershipData || didLoadRef.current) return;
    didLoadRef.current = true;
    void load();
  }, [canLoadMembershipData]);

  const activeMembership = membership?.status === "active" ? membership : null;
  const status = useMemo(() => statusLabel(membership?.status), [membership?.status]);

  if (isCheckingAuth || !session) {
    return (
      <div className="flex min-h-[40vh] items-center justify-center">
        <LoaderCircle className="size-5 animate-spin text-slate-400" />
      </div>
    );
  }

  return (
    <section className="space-y-6">
      <PageHeader
        eyebrow="Membership"
        title="会员中心"
        description="查看当前会员状态、周期额度、到期时间和可兑换的会员套餐。会员兑换码请到兑换中心输入。"
        actions={
          <>
            <Button variant="outline" className="h-10 rounded-xl border-stone-200 bg-white/85" disabled={isLoading} onClick={() => void load()}>
              <RefreshCw className={isLoading ? "size-4 animate-spin" : "size-4"} />
              刷新
            </Button>
            <Button asChild className="h-10 rounded-xl bg-slate-950 text-white hover:bg-slate-800">
              <Link href="/redeem">
                <Gift className="size-4" />
                兑换中心
              </Link>
            </Button>
          </>
        }
      />

      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
        <StatCard label="当前会员" value={isLoading ? "加载中..." : status} icon={<Crown className="size-5" />} tone={activeMembership ? "amber" : "slate"} />
        <StatCard label="会员额度" value={user?.member_image_quota ?? 0} hint="当前周期剩余额度" icon={<Sparkles className="size-5" />} tone="teal" />
        <StatCard label="总图片额度" value={user?.total_image_quota ?? user?.image_quota ?? 0} hint="普通额度 + 会员额度" icon={<Sparkles className="size-5" />} tone="blue" />
        <StatCard label="周期结束" value={formatDate(user?.membership_period_ends_at)} icon={<CalendarClock className="size-5" />} tone="emerald" />
      </div>

      <DataPanel title="当前会员" description="会员额度按套餐周期刷新，不会结转到下一个周期。">
        {isLoading ? (
          <div className="flex items-center justify-center gap-3 px-6 py-16 text-sm text-slate-500">
            <LoaderCircle className="size-5 animate-spin" />
            正在加载会员状态
          </div>
        ) : activeMembership ? (
          <div className="grid gap-4 p-5 md:grid-cols-2">
            <div className="rounded-3xl border border-amber-100 bg-amber-50/70 p-5">
              <div className="flex items-center gap-2">
                <Badge variant="warning" className="rounded-md">有效会员</Badge>
                <span className="text-sm font-bold text-slate-900">{activeMembership.plan_name}</span>
              </div>
              <div className="mt-4 text-sm leading-6 text-slate-600">
                每 {activeMembership.period_days} 天刷新 {activeMembership.period_image_quota} 张会员图片额度，有效期 {activeMembership.duration_days} 天。
              </div>
            </div>
            <div className="rounded-3xl border border-slate-200 bg-white p-5 text-sm leading-7 text-slate-600">
              <div>激活时间：{formatDate(activeMembership.activated_at)}</div>
              <div>周期结束：{formatDate(activeMembership.current_period_ends_at)}</div>
              <div>会员到期：{formatDate(activeMembership.expires_at)}</div>
            </div>
          </div>
        ) : (
          <div className="p-5">
            <EmptyState
              title="暂无有效会员"
              description="获取会员兑换码后，可在兑换中心激活套餐并领取周期会员图片额度。"
              icon={<Crown className="size-7" />}
              action={
                <Button asChild className="rounded-xl bg-slate-950 text-white hover:bg-slate-800">
                  <Link href="/redeem">前往兑换中心</Link>
                </Button>
              }
            />
          </div>
        )}
      </DataPanel>

      <DataPanel title="会员套餐" description="管理员启用的套餐会显示在这里，兑换码由管理员生成并发放。">
        <div className="grid gap-4 p-5 md:grid-cols-2 xl:grid-cols-3">
          {plans.map((plan) => (
            <div key={plan.id} className="rounded-3xl border border-slate-200/80 bg-white p-5 shadow-sm">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <h3 className="text-lg font-black text-slate-950">{plan.name}</h3>
                  <p className="mt-1 text-sm leading-6 text-slate-500">{plan.description || "管理员暂未填写说明。"}</p>
                </div>
                <Badge variant="success" className="rounded-md">可兑换</Badge>
              </div>
              <div className="mt-4 grid grid-cols-3 gap-2 text-center text-xs font-semibold text-slate-500">
                <div className="rounded-2xl bg-slate-50 p-3"><div className="text-lg font-black text-slate-950">{plan.duration_days}</div>有效天数</div>
                <div className="rounded-2xl bg-slate-50 p-3"><div className="text-lg font-black text-slate-950">{plan.period_days}</div>周期天数</div>
                <div className="rounded-2xl bg-slate-50 p-3"><div className="text-lg font-black text-slate-950">{plan.period_image_quota}</div>周期额度</div>
              </div>
            </div>
          ))}
        </div>
      </DataPanel>
    </section>
  );
}
