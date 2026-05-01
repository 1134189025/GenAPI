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
import {
  claimDailyCheckin,
  fetchCheckinStatus,
  fetchMembershipPlans,
  fetchUserMembership,
  type CheckinStatus,
  type CheckinStatusResponse,
  type ManagedUser,
  type MembershipPlan,
  type UserMembership,
} from "@/lib/api";
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

function statusFromCheckinResponse(response: CheckinStatusResponse): CheckinStatus | null {
  if (response.status) return response.status;
  if (
    "enabled" in response ||
    "checkin_enabled" in response ||
    "can_checkin" in response ||
    "checked_in_today" in response ||
    "already_checked_in" in response ||
    "reward_image_quota" in response ||
    "today_reward_image_quota" in response
  ) {
    return response as CheckinStatus;
  }
  return null;
}

function numberFromStatus(status: CheckinStatus | null, keys: string[], fallback = 0) {
  if (!status) return fallback;
  for (const key of keys) {
    const value = status[key];
    if (typeof value === "number" && Number.isFinite(value)) return value;
    if (typeof value === "string" && value.trim()) {
      const numeric = Number(value);
      if (Number.isFinite(numeric)) return numeric;
    }
  }
  return fallback;
}

function booleanFromStatus(status: CheckinStatus | null, keys: string[], fallback = false) {
  if (!status) return fallback;
  for (const key of keys) {
    const value = status[key];
    if (typeof value === "boolean") return value;
    if (typeof value === "number") return value !== 0;
    if (typeof value === "string") {
      const normalized = value.trim().toLowerCase();
      if (["1", "true", "yes", "on"].includes(normalized)) return true;
      if (["0", "false", "no", "off"].includes(normalized)) return false;
    }
  }
  return fallback;
}

function stringFromStatus(status: CheckinStatus | null, keys: string[]) {
  if (!status) return "";
  for (const key of keys) {
    const value = status[key];
    if (typeof value === "string" && value.trim()) return value.trim();
  }
  return "";
}

function formatNextCheckinTime(status: CheckinStatus | null) {
  const explicit = stringFromStatus(status, ["next_available_at", "next_checkin_at"]);
  if (explicit) return formatDate(explicit);

  const checkinDate = stringFromStatus(status, ["next_checkin_date", "checkin_date"]);
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(checkinDate);
  if (!match) return checkinDate || "—";

  const date = new Date(Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3]) + 1));
  return `${date.getUTCFullYear()}-${String(date.getUTCMonth() + 1).padStart(2, "0")}-${String(date.getUTCDate()).padStart(2, "0")} 00:00`;
}

export default function MembershipPage() {
  const { isCheckingAuth, session } = useAuthGuard();
  const didLoadRef = useRef(false);
  const [user, setUser] = useState<ManagedUser | null>(null);
  const [membership, setMembership] = useState<UserMembership | null>(null);
  const [plans, setPlans] = useState<MembershipPlan[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [checkinStatus, setCheckinStatus] = useState<CheckinStatus | null>(null);
  const [isCheckinLoading, setIsCheckinLoading] = useState(true);
  const [checkinError, setCheckinError] = useState<string | null>(null);
  const [isClaimingCheckin, setIsClaimingCheckin] = useState(false);
  const canLoadMembershipData = !isCheckingAuth && Boolean(session);

  const loadMembershipData = async () => {
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

  const loadCheckinStatus = async () => {
    setIsCheckinLoading(true);
    setCheckinError(null);
    try {
      const data = await fetchCheckinStatus();
      setCheckinStatus(statusFromCheckinResponse(data));
    } catch (error) {
      setCheckinStatus(null);
      setCheckinError(error instanceof Error ? error.message : "加载签到状态失败");
    } finally {
      setIsCheckinLoading(false);
    }
  };

  const load = async () => {
    await Promise.all([loadMembershipData(), loadCheckinStatus()]);
  };

  const handleClaimCheckin = async () => {
    if (!checkinStatus) return;
    setIsClaimingCheckin(true);
    try {
      const data = await claimDailyCheckin();
      const nextStatus = statusFromCheckinResponse(data);
      if (nextStatus) setCheckinStatus(nextStatus);
      if (data.user) setUser(data.user);
      const reward = numberFromStatus(nextStatus ?? checkinStatus, [
        "today_reward_image_quota",
        "reward_image_quota",
        "daily_image_quota",
      ]);
      toast.success(reward > 0 ? `签到成功，已领取 ${reward} 张图片额度` : "签到成功");
      await Promise.all([loadMembershipData(), loadCheckinStatus()]);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "签到失败");
    } finally {
      setIsClaimingCheckin(false);
    }
  };

  useEffect(() => {
    if (!canLoadMembershipData || didLoadRef.current) return;
    didLoadRef.current = true;
    void load();
  }, [canLoadMembershipData]);

  const activeMembership = membership?.status === "active" ? membership : null;
  const membershipStatus = useMemo(() => statusLabel(membership?.status), [membership?.status]);
  const checkinEnabled = booleanFromStatus(checkinStatus, ["enabled", "checkin_enabled"], false);
  const checkedInToday = booleanFromStatus(checkinStatus, ["checked_in_today", "checked_in", "already_checked_in"], false);
  const canCheckin = checkinEnabled && booleanFromStatus(checkinStatus, ["can_checkin"], !checkedInToday);
  const todayReward = numberFromStatus(checkinStatus, [
    "today_reward_image_quota",
    "reward_image_quota",
    "daily_image_quota",
  ]);
  const streakDays = numberFromStatus(checkinStatus, ["current_streak_days", "streak_days"]);
  const nextAvailableAt = canCheckin && !checkedInToday ? "现在可签" : formatNextCheckinTime(checkinStatus);
  const checkinTimezone = stringFromStatus(checkinStatus, ["timezone"]) || "Asia/Shanghai";
  const streakBonusEnabled = booleanFromStatus(checkinStatus, ["streak_bonus_enabled"], false);
  const streakBonusDays = numberFromStatus(checkinStatus, ["streak_bonus_days"]);
  const streakBonusQuota = numberFromStatus(checkinStatus, ["streak_bonus_image_quota", "bonus_image_quota"]);

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
            <Button variant="outline" className="h-10 rounded-xl border-stone-200 bg-white/85" disabled={isLoading || isCheckinLoading} onClick={() => void load()}>
              <RefreshCw className={isLoading || isCheckinLoading ? "size-4 animate-spin" : "size-4"} />
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
        <StatCard label="当前会员" value={isLoading ? "加载中..." : membershipStatus} icon={<Crown className="size-5" />} tone={activeMembership ? "amber" : "slate"} />
        <StatCard label="会员额度" value={user?.member_image_quota ?? 0} hint="当前周期剩余额度" icon={<Sparkles className="size-5" />} tone="teal" />
        <StatCard label="总图片额度" value={user?.total_image_quota ?? user?.image_quota ?? 0} hint="普通额度 + 会员额度" icon={<Sparkles className="size-5" />} tone="blue" />
        <StatCard label="周期结束" value={formatDate(user?.membership_period_ends_at)} icon={<CalendarClock className="size-5" />} tone="emerald" />
      </div>

      <DataPanel title="每日签到" description="每日签到领取普通图片额度，连续签到可获得额外奖励。">
        {isCheckinLoading ? (
          <div className="flex items-center justify-center gap-3 px-6 py-16 text-sm text-slate-500">
            <LoaderCircle className="size-5 animate-spin" />
            正在加载签到状态
          </div>
        ) : checkinError ? (
          <div className="p-5">
            <EmptyState
              title="签到状态加载失败"
              description={checkinError}
              icon={<Gift className="size-7" />}
              action={
                <Button className="rounded-xl bg-slate-950 text-white hover:bg-slate-800" onClick={() => void loadCheckinStatus()}>
                  <RefreshCw className="size-4" />
                  重试
                </Button>
              }
            />
          </div>
        ) : !checkinStatus || !checkinEnabled ? (
          <div className="p-5">
            <EmptyState
              title="签到奖励暂未开启"
              description="管理员开启后，用户可在这里领取每日图片额度。"
              icon={<Gift className="size-7" />}
            />
          </div>
        ) : (
          <div className="grid gap-4 p-5 lg:grid-cols-[1.2fr_1fr]">
            <div className="rounded-3xl border border-emerald-100 bg-emerald-50/70 p-5">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div>
                  <div className="text-sm font-semibold text-emerald-700">今日奖励</div>
                  <div className="mt-2 text-3xl font-black text-slate-950">{todayReward} 张</div>
                </div>
                {checkedInToday ? (
                  <Badge variant="success" className="rounded-md">今日已领取</Badge>
                ) : (
                  <Button className="h-10 rounded-xl bg-slate-950 text-white hover:bg-slate-800" disabled={!canCheckin || isClaimingCheckin} onClick={() => void handleClaimCheckin()}>
                    {isClaimingCheckin ? <LoaderCircle className="size-4 animate-spin" /> : <Gift className="size-4" />}
                    签到领取
                  </Button>
                )}
              </div>
              <p className="mt-4 text-sm leading-6 text-slate-600">
                奖励会发放到普通图片额度，可用于生成或编辑图片。
              </p>
            </div>
            <div className="grid gap-3 text-sm text-slate-600 sm:grid-cols-3 lg:grid-cols-1">
              <div className="rounded-2xl border border-slate-200 bg-white p-4">
                <div className="text-xs font-semibold text-slate-500">连续天数</div>
                <div className="mt-2 text-xl font-black text-slate-950">{streakDays} 天</div>
              </div>
              <div className="rounded-2xl border border-slate-200 bg-white p-4">
                <div className="text-xs font-semibold text-slate-500">下次可签</div>
                <div className="mt-2 text-sm font-bold text-slate-950">{nextAvailableAt}</div>
                <div className="mt-1 text-xs text-slate-500">{checkinTimezone}</div>
              </div>
              <div className="rounded-2xl border border-slate-200 bg-white p-4">
                <div className="text-xs font-semibold text-slate-500">连续奖励</div>
                <div className="mt-2 text-sm font-bold text-slate-950">
                  {streakBonusEnabled && streakBonusDays > 0 ? `${streakBonusDays} 天额外 +${streakBonusQuota} 张` : "未启用"}
                </div>
              </div>
            </div>
          </div>
        )}
      </DataPanel>

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
