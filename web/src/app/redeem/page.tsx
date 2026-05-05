"use client";

import { useEffect, useRef, useState } from "react";
import { AlertTriangle, BadgeCheck, History, LoaderCircle, Sparkles, TicketCheck, Zap } from "lucide-react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { fetchMe, fetchRedeemHistory, redeemCode, type ManagedUser, type RedeemCode } from "@/lib/api";
import { formatQuotaAsGgb } from "@/lib/ggb";
import { cn } from "@/lib/utils";
import { useAuthGuard } from "@/lib/use-auth-guard";

type RedeemFeedback = {
  type: "success" | "error";
  title: string;
  message: string;
};

function typeLabel(type: string) {
  if (type === "image_quota") return "狗狗币余额";
  if (type === "concurrency") return "图片并发";
  if (type === "membership") return "会员兑换";
  return "邀请码";
}

function quotaLabel(user: ManagedUser | null) {
  if (!user) return "—";
  return user.role === "admin" ? "不限" : formatQuotaAsGgb(user.total_image_quota ?? user.image_quota ?? 0);
}

function concurrencyLabel(user: ManagedUser | null) {
  if (!user) return "—";
  return user.role === "admin" ? "不限" : String(user.image_concurrency ?? 0);
}

function formatHistoryTime(value: string | null) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

export default function RedeemPage() {
  const { isCheckingAuth, session } = useAuthGuard();
  const didLoadRef = useRef(false);
  const [user, setUser] = useState<ManagedUser | null>(null);
  const [items, setItems] = useState<RedeemCode[]>([]);
  const [code, setCode] = useState("");
  const [isLoading, setIsLoading] = useState(true);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [feedback, setFeedback] = useState<RedeemFeedback | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const canLoadRedeemData = !isCheckingAuth && Boolean(session);

  const load = async () => {
    setIsLoading(true);
    setLoadError(null);
    try {
      const [me, history] = await Promise.all([fetchMe(), fetchRedeemHistory()]);
      setUser(me.user);
      setItems(history.items);
    } catch (error) {
      const message = error instanceof Error ? error.message : "加载兑换信息失败";
      setLoadError(message);
      toast.error(message);
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    if (!canLoadRedeemData || didLoadRef.current) return;
    didLoadRef.current = true;
    void load();
  }, [canLoadRedeemData]);

  const handleRedeem = async () => {
    const normalizedCode = code.trim();
    if (!normalizedCode) {
      const message = "请输入兑换码";
      setFeedback({ type: "error", title: "缺少兑换码", message });
      toast.error(message);
      return;
    }

    setIsSubmitting(true);
    setFeedback(null);
    try {
      const data = await redeemCode(normalizedCode);
      setUser(data.user);
      setCode("");
      const history = await fetchRedeemHistory();
      setItems(history.items);
      const rewardLabel =
        data.redeem.type === "membership"
          ? "会员套餐已激活"
          : data.redeem.type === "image_quota"
            ? `${typeLabel(data.redeem.type)} +${formatQuotaAsGgb(data.redeem.value)}`
            : `${typeLabel(data.redeem.type)} +${data.redeem.value}`;
      setFeedback({
        type: "success",
        title: "兑换成功",
        message: `${rewardLabel} 已到账。当前狗狗币余额 ${quotaLabel(data.user)}，图片并发 ${concurrencyLabel(data.user)}。`,
      });
      toast.success("兑换成功");
    } catch (error) {
      const message = error instanceof Error ? error.message : "兑换失败";
      setFeedback({ type: "error", title: "兑换失败", message });
      toast.error(message);
    } finally {
      setIsSubmitting(false);
    }
  };

  if (isCheckingAuth || !session) {
    return (
      <div className="flex min-h-[40vh] items-center justify-center">
        <LoaderCircle className="size-5 animate-spin text-slate-400" />
      </div>
    );
  }

  return (
    <section className="mx-auto flex w-full max-w-[1500px] flex-col gap-5 pb-8">
      <div className="overflow-hidden rounded-[32px] border border-white/80 bg-white/90 p-5 shadow-[0_28px_90px_-52px_rgba(15,23,42,0.65)] backdrop-blur-xl sm:p-6">
        <div className="flex flex-col gap-5 lg:flex-row lg:items-end lg:justify-between">
          <div className="max-w-3xl">
            <h1 className="text-2xl font-black tracking-tight text-slate-950 sm:text-3xl">兑换狗狗币余额与并发能力</h1>
            <p className="mt-2 text-sm leading-6 text-slate-500">
              狗狗币余额码、并发码和会员兑换码可在这里使用；邀请码仅用于注册流程。
            </p>
          </div>
          <div className="rounded-[28px] border border-slate-200/70 bg-slate-50/80 p-4 lg:w-[360px]">
            <div className="text-[11px] font-black uppercase tracking-[0.2em] text-slate-400">当前账号</div>
            <div className="mt-2 text-lg font-black text-slate-950">{session.role === "admin" ? "管理员" : "普通用户"}</div>
          </div>
        </div>

      </div>

      {loadError ? (
        <FeedbackBanner
          feedback={{
            type: "error",
            title: "加载失败",
            message: loadError,
          }}
          action={
            <Button
              variant="outline"
              className="h-9 rounded-xl border-rose-200 bg-white text-rose-700 hover:bg-rose-50"
              onClick={() => void load()}
            >
              重试
            </Button>
          }
        />
      ) : null}

      <section className="overflow-hidden rounded-[32px] border border-white/80 bg-white/90 shadow-[0_28px_90px_-52px_rgba(15,23,42,0.65)] backdrop-blur-xl">
        <div className="border-b border-slate-200/70 p-5 sm:p-6">
          <h2 className="text-xl font-black tracking-tight text-slate-950">输入兑换码</h2>
          <p className="mt-1 text-sm leading-6 text-slate-500">兑换码会去除首尾空格后提交。</p>
        </div>

        <div className="space-y-5 p-5 sm:p-6">
          {feedback ? <FeedbackBanner feedback={feedback} /> : null}

          <div className="grid gap-3 sm:grid-cols-[1fr_auto]">
            <Input
              value={code}
              onChange={(event) => setCode(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") void handleRedeem();
              }}
              placeholder="IMG-..."
              className="h-12 rounded-2xl border-slate-200 bg-white font-mono text-base font-bold uppercase tracking-[0.08em] text-slate-950 placeholder:font-sans placeholder:normal-case placeholder:tracking-normal focus-visible:ring-teal-500/30"
            />
            <Button
              className="h-12 rounded-2xl bg-slate-950 px-6 font-black text-white shadow-[0_20px_50px_-26px_rgba(15,23,42,0.9)] hover:bg-teal-700"
              disabled={isSubmitting}
              onClick={() => void handleRedeem()}
            >
              {isSubmitting ? <LoaderCircle className="size-4 animate-spin" /> : <TicketCheck className="size-4" />}
              兑换
            </Button>
          </div>

        </div>
      </section>

      <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
        <RedeemStatCard
          label="狗狗币余额"
          value={isLoading ? "加载中..." : quotaLabel(user)}
          icon={<Sparkles className="size-5" />}
          tone="teal"
        />
        <RedeemStatCard
          label="图片并发"
          value={isLoading ? "加载中..." : concurrencyLabel(user)}
          icon={<Zap className="size-5" />}
          tone="amber"
        />
        <RedeemStatCard
          label="当前活跃请求"
          value={user?.active_image_requests ?? 0}
          icon={<LoaderCircle className={cn("size-5", (user?.active_image_requests ?? 0) > 0 && "animate-spin")} />}
          tone={(user?.active_image_requests ?? 0) > 0 ? "blue" : "slate"}
        />
        <RedeemStatCard
          label="兑换记录"
          value={isLoading ? "加载中..." : items.length}
          icon={<History className="size-5" />}
          tone="slate"
        />
      </div>

      <section className="overflow-hidden rounded-[32px] border border-white/80 bg-white/90 shadow-[0_28px_90px_-52px_rgba(15,23,42,0.65)] backdrop-blur-xl">
        <div className="flex flex-col gap-3 border-b border-slate-200/70 p-5 sm:flex-row sm:items-center sm:justify-between sm:p-6">
          <div>
            <h2 className="text-xl font-black tracking-tight text-slate-950">兑换记录</h2>
          </div>
          <Badge variant="secondary" className="w-fit rounded-full bg-slate-100 px-3 py-1 text-slate-600">
            {items.length} 条记录
          </Badge>
        </div>

        <div className="space-y-2 p-4 sm:space-y-3 sm:p-6">
          {isLoading ? (
            <div className="flex min-h-[180px] items-center justify-center rounded-[22px] bg-slate-50 text-slate-400 sm:min-h-[220px] sm:rounded-[26px]">
              <LoaderCircle className="size-5 animate-spin" />
            </div>
          ) : items.length === 0 ? (
            <div className="rounded-[22px] border border-dashed border-slate-300 bg-slate-50/80 px-5 py-8 text-center text-sm leading-6 text-slate-500 sm:rounded-[26px] sm:px-6 sm:py-10">
              暂无兑换记录
            </div>
          ) : (
            items.map((item) => <RedeemHistoryItem key={item.id} item={item} />)
          )}
        </div>
      </section>
    </section>
  );
}

function RedeemStatCard({
  label,
  value,
  icon,
  tone,
}: {
  label: string;
  value: string | number;
  icon: React.ReactNode;
  tone: "teal" | "amber" | "blue" | "slate";
}) {
  return (
    <div className="rounded-[20px] border border-slate-200/70 bg-white/80 p-3 shadow-sm sm:rounded-[24px] sm:p-4">
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="text-[11px] font-black uppercase tracking-[0.18em] text-slate-400">{label}</div>
          <div className="mt-1 text-xl font-black tracking-tight text-slate-950 sm:mt-2 sm:text-2xl">{value}</div>
        </div>
        <div
          className={cn(
            "grid size-10 shrink-0 place-items-center rounded-2xl sm:size-11",
            tone === "teal" && "bg-teal-50 text-teal-700",
            tone === "amber" && "bg-amber-50 text-amber-700",
            tone === "blue" && "bg-blue-50 text-blue-700",
            tone === "slate" && "bg-slate-100 text-slate-600",
          )}
        >
          {icon}
        </div>
      </div>
    </div>
  );
}

function FeedbackBanner({ feedback, action }: { feedback: RedeemFeedback; action?: React.ReactNode }) {
  const success = feedback.type === "success";
  const Icon = success ? BadgeCheck : AlertTriangle;

  return (
    <div
      role={success ? "status" : "alert"}
      className={cn(
        "flex flex-col gap-3 rounded-[24px] border px-4 py-4 sm:flex-row sm:items-start sm:justify-between",
        success ? "border-emerald-200 bg-emerald-50 text-emerald-800" : "border-rose-200 bg-rose-50 text-rose-800",
      )}
    >
      <div className="flex gap-3">
        <div className={cn("mt-0.5 grid size-9 shrink-0 place-items-center rounded-2xl bg-white/70", success ? "text-emerald-700" : "text-rose-700")}>
          <Icon className="size-5" />
        </div>
        <div>
          <div className="text-sm font-black">{feedback.title}</div>
          <p className="mt-1 text-sm leading-6 opacity-85">{feedback.message}</p>
        </div>
      </div>
      {action}
    </div>
  );
}

function RedeemHistoryItem({ item }: { item: RedeemCode }) {
  const typeTone =
    item.type === "image_quota" ? "bg-teal-50 text-teal-700" : item.type === "membership" ? "bg-blue-50 text-blue-700" : item.type === "concurrency" ? "bg-amber-50 text-amber-700" : "bg-slate-100 text-slate-600";

  return (
    <article className="rounded-[20px] border border-slate-200/70 bg-white/90 p-3 shadow-sm sm:rounded-[24px] sm:p-4 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
      <div className="flex min-w-0 items-start gap-3">
        <div className="grid size-11 shrink-0 place-items-center rounded-2xl bg-slate-100 text-slate-500">
          <TicketCheck className="size-5" />
        </div>
        <div className="min-w-0">
          <div className="truncate font-mono text-sm font-black tracking-[0.08em] text-slate-950">{item.code_preview}</div>
          <div className="mt-1 text-xs text-slate-500">
            {item.used ? "已使用" : "未使用"} · {formatHistoryTime(item.used_at || item.created_at)}
          </div>
        </div>
      </div>
      <div className="flex shrink-0 items-center gap-2">
        <Badge variant="secondary" className={cn("rounded-full px-3 py-1 font-bold", typeTone)}>
          {typeLabel(item.type)}
        </Badge>
        <span className="rounded-full bg-slate-950 px-3 py-1 text-sm font-black text-white">
          {item.type === "image_quota" ? `+${formatQuotaAsGgb(item.value)}` : `+${item.value}`}
        </span>
      </div>
    </article>
  );
}
