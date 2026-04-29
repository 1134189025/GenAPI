"use client";

import { AlertTriangle, CheckCircle2, ExternalLink, LoaderCircle, RefreshCw, Rocket, RotateCcw } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";

import { DataPanel } from "@/components/common/data-panel";
import { EmptyState } from "@/components/common/empty-state";
import { Button } from "@/components/ui/button";
import {
  fetchUpdateJob,
  fetchUpdateStatus,
  startSystemUpdate,
  type UpdateJob,
  type UpdateStatusResponse,
} from "@/lib/api";

const ACTIVE_JOB_STATUSES = new Set(["pending", "running", "in_progress", "updating"]);

function isActiveJob(job: UpdateJob | null | undefined) {
  return Boolean(job?.status && ACTIVE_JOB_STATUSES.has(String(job.status)));
}

function formatDate(value?: string) {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("zh-CN", { hour12: false });
}

function statusLabel(job: UpdateJob | null | undefined) {
  if (!job) return "无任务";
  const status = String(job.status || "");
  if (ACTIVE_JOB_STATUSES.has(status)) return "运行中";
  if (status === "succeeded" || status === "success") return "已完成";
  if (status === "failed") return "失败";
  if (status === "cancelled") return "已取消";
  return status || "未知";
}

function Pill({ children, tone = "slate" }: { children: React.ReactNode; tone?: "slate" | "emerald" | "amber" | "rose" | "blue" }) {
  const toneClass = {
    amber: "border-amber-200 bg-amber-50 text-amber-800",
    blue: "border-blue-200 bg-blue-50 text-blue-800",
    emerald: "border-emerald-200 bg-emerald-50 text-emerald-800",
    rose: "border-rose-200 bg-rose-50 text-rose-800",
    slate: "border-slate-200 bg-slate-50 text-slate-700",
  }[tone];

  return <span className={`inline-flex items-center rounded-full border px-2.5 py-1 text-xs font-bold ${toneClass}`}>{children}</span>;
}

export function UpdateCard() {
  const [data, setData] = useState<UpdateStatusResponse | null>(null);
  const [activeJob, setActiveJob] = useState<UpdateJob | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [isStarting, setIsStarting] = useState(false);

  const latestJob = activeJob ?? data?.jobs?.[0] ?? null;
  const running = isActiveJob(latestJob);
  const status = data?.status;
  const preflight = data?.preflight;
  const canStart = Boolean(status?.enabled && status.update_available && preflight?.ok && !running && !isStarting);

  const recentLogs = useMemo(() => (latestJob?.logs ?? []).slice(-8), [latestJob?.logs]);

  const loadStatus = async (force = false) => {
    if (force) {
      setIsRefreshing(true);
    } else {
      setIsLoading(true);
    }
    try {
      const next = await fetchUpdateStatus(force);
      setData(next);
      setActiveJob(next.jobs.find(isActiveJob) ?? null);
      if (force) toast.success("更新状态已刷新");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "加载版本更新状态失败");
    } finally {
      setIsLoading(false);
      setIsRefreshing(false);
    }
  };

  useEffect(() => {
    void loadStatus(false);
  }, []);

  useEffect(() => {
    if (!activeJob?.id || !isActiveJob(activeJob)) return;

    const timer = window.setInterval(async () => {
      try {
        const next = await fetchUpdateJob(activeJob.id);
        setActiveJob(next.job);
        if (!isActiveJob(next.job)) {
          void loadStatus(true);
        }
      } catch (error) {
        toast.error(error instanceof Error ? error.message : "查询更新任务失败");
      }
    }, 2000);

    return () => window.clearInterval(timer);
  }, [activeJob?.id, activeJob?.status]);

  const handleStart = async () => {
    if (!canStart) return;
    setIsStarting(true);
    try {
      const result = await startSystemUpdate();
      setActiveJob(result.job);
      toast.success("更新任务已启动");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "启动更新失败");
    } finally {
      setIsStarting(false);
    }
  };

  const disabledReason = !status?.enabled
    ? "需要设置 GENAPI_ENABLE_WEB_UPDATER=true 后才能使用一键更新。"
    : status.error
      ? status.error
    : !status.update_available
      ? "当前已是最新版本，暂无可安装更新。"
      : !preflight?.ok
        ? "预检未通过，请先处理错误项。"
        : running
          ? "已有更新任务正在运行。"
          : "";

  if (isLoading && !data) {
    return (
      <DataPanel title="版本更新中心" description="正在检查当前部署版本、GitHub Release 和 Docker 预检状态。">
        <div className="p-5">
          <EmptyState title="正在加载更新状态" description="同步版本、预检和最近任务。 " icon={<LoaderCircle className="size-7 animate-spin" />} />
        </div>
      </DataPanel>
    );
  }

  return (
    <DataPanel
      title="版本更新中心"
      description="面向 Docker Compose 部署的 0.1.4 更新入口；未启用时保持手动更新模式。"
      toolbar={
        <>
          <Button
            variant="outline"
            className="h-9 rounded-xl border-stone-200 bg-white px-4 text-stone-700"
            disabled={isRefreshing}
            onClick={() => void loadStatus(true)}
          >
            {isRefreshing ? <LoaderCircle className="size-4 animate-spin" /> : <RefreshCw className="size-4" />}
            刷新
          </Button>
          <Button className="h-9 rounded-xl bg-stone-950 px-4 text-white hover:bg-stone-800" disabled={!canStart} onClick={() => void handleStart()}>
            {isStarting ? <LoaderCircle className="size-4 animate-spin" /> : <Rocket className="size-4" />}
            一键更新
          </Button>
        </>
      }
    >
      <div className="space-y-5 p-6">
        <div className="grid gap-3 md:grid-cols-3">
          <div className="rounded-2xl border border-stone-200 bg-stone-50/80 p-4">
            <div className="text-xs font-bold text-stone-500">当前版本</div>
            <div className="mt-2 text-2xl font-black text-stone-950">{status?.current_version ?? "-"}</div>
          </div>
          <div className="rounded-2xl border border-stone-200 bg-white p-4">
            <div className="text-xs font-bold text-stone-500">最新版本</div>
            <div className="mt-2 flex items-center gap-2 text-2xl font-black text-stone-950">
              {status?.latest_version ?? "未检测"}
              {status?.update_available ? <Pill tone="blue">可更新</Pill> : <Pill tone="emerald">已同步</Pill>}
            </div>
          </div>
          <div className="rounded-2xl border border-stone-200 bg-white p-4">
            <div className="text-xs font-bold text-stone-500">运行模式</div>
            <div className="mt-2 flex flex-wrap items-center gap-2">
              <Pill tone={status?.enabled ? "emerald" : "amber"}>{status?.enabled ? "已启用" : "手动模式"}</Pill>
              <Pill>{status?.mode ?? "manual"}</Pill>
            </div>
          </div>
        </div>

        {disabledReason ? <div className="rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm leading-6 text-amber-800">{disabledReason}</div> : null}

        <div className="grid gap-4 xl:grid-cols-[1.05fr_0.95fr]">
          <div className="space-y-3 rounded-2xl border border-stone-200 bg-white p-4">
            <div className="flex items-center justify-between gap-3">
              <div>
                <h3 className="text-sm font-black text-stone-950">预检</h3>
                <p className="mt-1 text-xs text-stone-500">检查 Docker、Compose 目录和更新开关。</p>
              </div>
              <Pill tone={preflight?.ok ? "emerald" : "rose"}>{preflight?.ok ? "通过" : "未通过"}</Pill>
            </div>

            {preflight?.errors?.length ? (
              <div className="space-y-2">
                {preflight.errors.map((item) => (
                  <div key={item} className="flex gap-2 rounded-xl border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-800">
                    <AlertTriangle className="mt-0.5 size-4 shrink-0" />
                    <span>{item}</span>
                  </div>
                ))}
              </div>
            ) : (
              <div className="flex gap-2 rounded-xl border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm text-emerald-800">
                <CheckCircle2 className="mt-0.5 size-4 shrink-0" />
                <span>核心预检通过，可以在满足版本条件后执行更新。</span>
              </div>
            )}

            {preflight?.warnings?.length ? (
              <div className="space-y-2">
                {preflight.warnings.map((item) => (
                  <div key={item} className="rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800">
                    {item}
                  </div>
                ))}
              </div>
            ) : null}
          </div>

          <div className="space-y-3 rounded-2xl border border-stone-200 bg-stone-950 p-4 text-white">
            <div className="flex items-center justify-between gap-3">
              <div>
                <h3 className="text-sm font-black">最近任务</h3>
                <p className="mt-1 text-xs text-stone-400">活动任务每 2 秒自动轮询。</p>
              </div>
              <Pill tone={running ? "blue" : latestJob?.status === "failed" ? "rose" : "slate"}>{statusLabel(latestJob)}</Pill>
            </div>
            {latestJob ? (
              <div className="space-y-2 text-sm">
                <div className="flex justify-between gap-4 text-stone-300">
                  <span>目标</span>
                  <span className="font-bold text-white">{latestJob.target_version || latestJob.target_tag || "-"}</span>
                </div>
                <div className="flex justify-between gap-4 text-stone-300">
                  <span>更新时间</span>
                  <span className="text-right text-white">{formatDate(latestJob.updated_at ?? latestJob.created_at)}</span>
                </div>
                {latestJob.message || latestJob.error ? <div className="rounded-xl bg-white/10 px-3 py-2 text-stone-100">{latestJob.error ?? latestJob.message}</div> : null}
              </div>
            ) : (
              <EmptyState title="暂无更新任务" description="启动一键更新后，任务进度和日志会显示在这里。" className="border-white/10 bg-white/5 text-white" />
            )}
          </div>
        </div>

        <div className="grid gap-4 lg:grid-cols-[1fr_18rem]">
          <div className="rounded-2xl border border-stone-200 bg-white p-4">
            <div className="mb-3 text-sm font-black text-stone-950">最近日志</div>
            {recentLogs.length ? (
              <div className="max-h-48 space-y-1 overflow-auto rounded-xl bg-slate-950 p-3 font-mono text-xs leading-6 text-slate-100">
                {recentLogs.map((line, index) => (
                  <div key={`${index}-${line}`}>{line}</div>
                ))}
              </div>
            ) : (
              <div className="rounded-xl border border-dashed border-stone-200 bg-stone-50 px-4 py-5 text-sm text-stone-500">暂无任务日志。</div>
            )}
          </div>

          <div className="space-y-3 rounded-2xl border border-stone-200 bg-white p-4">
            <div className="flex items-center gap-2 text-sm font-black text-stone-950">
              <RotateCcw className="size-4 text-stone-500" />
              回滚
            </div>
            <p className="text-xs leading-6 text-stone-500">更新执行器会在健康检查失败时尝试回滚到上一镜像；人工回滚仍应通过部署环境完成。</p>
            {status?.release_url ? (
              <Button asChild variant="outline" className="h-9 w-full rounded-xl border-stone-200 bg-white text-stone-700">
                <a href={status.release_url} target="_blank" rel="noreferrer">
                  查看 Release
                  <ExternalLink className="size-4" />
                </a>
              </Button>
            ) : null}
          </div>
        </div>
      </div>
    </DataPanel>
  );
}
