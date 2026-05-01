"use client";

import { AlertTriangle, CheckCircle2, ExternalLink, FileText, LoaderCircle, Power, RefreshCw, Rocket, RotateCcw, Terminal } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";

import { DataPanel } from "@/components/common/data-panel";
import { EmptyState } from "@/components/common/empty-state";
import { Button } from "@/components/ui/button";
import {
  checkSystemUpdates,
  performSystemUpdate,
  restartSystemService,
  rollbackSystemUpdate,
  type UpdateStatus,
} from "@/lib/api";

const DOCKER_MANUAL_UPDATE_COMMAND = "git pull && docker compose pull app && docker compose up -d app";
const SOURCE_MANUAL_UPDATE_COMMAND = "git pull && restart service manually";
const SYSTEMD_UPDATE_CONFIRMATION = "系统更新会下载并安装最新发布包，完成后可能需要重启服务。确认立即更新？";
const DOCKER_UPDATE_CONFIRMATION = "容器更新会拉取最新镜像并重新创建容器，页面可能会短暂断开连接。确认立即更新？";

type PillTone = "slate" | "emerald" | "amber" | "rose" | "blue";

export type ManualUpdateGuidance = {
  title: string;
  description: string;
  command: string;
};

function formatDate(value?: string) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("zh-CN", { hour12: false });
}

function getDeploymentMode(status: UpdateStatus | undefined) {
  const rawMode = String(status?.deployment_mode ?? status?.mode ?? "").trim().toLowerCase();
  if (rawMode === "docker-compose") return "docker";
  return rawMode || "source";
}

function getDeploymentModeLabel(status: UpdateStatus | undefined) {
  const mode = getDeploymentMode(status);
  if (mode === "systemd") return "systemd";
  if (mode === "docker") return "容器部署";
  if (mode === "source" || mode === "manual") return "源码部署";
  return mode;
}

function getLatestVersion(status: UpdateStatus | undefined) {
  return status?.latest_version || status?.latest_tag || status?.release_info?.tag_name || status?.release_info?.name || "";
}

function getReleaseUrl(status: UpdateStatus | undefined) {
  return status?.release_info?.html_url || status?.release_url || "";
}

export function hasAvailableSystemUpdate(status: UpdateStatus | undefined) {
  return status?.has_update === true || status?.update_available === true;
}

export function isSystemdReleaseMode(status: UpdateStatus | undefined) {
  return ["systemd", "systemd-binary"].includes(getDeploymentMode(status)) && (status?.build_type ?? "source") === "release";
}

function isDockerMode(status: UpdateStatus | undefined) {
  return getDeploymentMode(status) === "docker";
}

function isDockerWebUpdateMode(status: UpdateStatus | undefined) {
  return isDockerMode(status) && status?.can_update === true && !status.disabled_reason && !status.error;
}

export function getUpdateActionAvailability(
  status: UpdateStatus | undefined,
  options: { busy?: boolean; needsRestart?: boolean } = {},
) {
  const busy = options.busy ?? false;
  const needsRestart = options.needsRestart ?? false;
  const systemdReleaseMode = isSystemdReleaseMode(status);
  const webUpdateMode = systemdReleaseMode || isDockerWebUpdateMode(status);
  const hasUpdate = hasAvailableSystemUpdate(status);
  const canUpdate = Boolean(
    webUpdateMode &&
      hasUpdate &&
      !needsRestart &&
      !busy &&
      (systemdReleaseMode ? status?.can_update !== false : status?.can_update === true),
  );
  const canRollback = Boolean(systemdReleaseMode && status?.can_update !== false && !busy);

  return { canUpdate, canRollback, webUpdateMode };
}

export function getManualUpdateGuidance(status: UpdateStatus | undefined): ManualUpdateGuidance | null {
  if (!status || isSystemdReleaseMode(status)) return null;

  if (isDockerMode(status)) {
    if (!isDockerWebUpdateMode(status)) {
      return {
        title: "容器部署需要手动更新",
        description: "当前部署缺少网页更新所需的 Docker 授权或 compose 配置。请先同步最新 docker-compose.yml，再在部署目录运行以下命令。",
        command: DOCKER_MANUAL_UPDATE_COMMAND,
      };
    }
    return null;
  }

  return {
    title: "源码部署需要手动更新",
    description: "当前构建不包含发布包更新器，请拉取代码后手动重启服务。",
    command: SOURCE_MANUAL_UPDATE_COMMAND,
  };
}

export function getUpdateActionHint(
  status: UpdateStatus | undefined,
  options: { busy?: boolean; needsRestart?: boolean } = {},
) {
  if (!status) return "";
  const busy = options.busy ?? false;
  const needsRestart = options.needsRestart ?? false;
  const manualGuidance = getManualUpdateGuidance(status);
  const { webUpdateMode } = getUpdateActionAvailability(status, { busy, needsRestart });
  const hasUpdate = hasAvailableSystemUpdate(status);

  if (status.error) return status.error;
  if (!webUpdateMode && manualGuidance) return manualGuidance.description;
  if (status.disabled_reason) return status.disabled_reason;
  if (status.can_update === false) return "当前部署暂不支持网页更新。";
  if (needsRestart) return "更新已安装，重启服务前不会再次执行更新。";
  if (!hasUpdate) return "当前已是最新版本，暂无可安装更新。";
  if (busy) return "更新操作正在执行，请等待当前操作完成。";
  return "";
}

export function getReleaseSyncState(status: UpdateStatus | undefined) {
  if (!status) return { label: "未检测", tone: "amber" as const };
  if (status.error) return { label: "检查失败", tone: "rose" as const };
  if (hasAvailableSystemUpdate(status)) return { label: "可更新", tone: "blue" as const };
  if (Boolean(getLatestVersion(status)) && (status.has_update === false || status.update_available === false)) {
    return { label: "已同步", tone: "emerald" as const };
  }
  if (status.warning) return { label: "需确认", tone: "amber" as const };
  return { label: "未检测", tone: "amber" as const };
}

export function getSystemUpdateConfirmationMessage(status: UpdateStatus | undefined) {
  if (isDockerMode(status)) return DOCKER_UPDATE_CONFIRMATION;
  return SYSTEMD_UPDATE_CONFIRMATION;
}

export function confirmSystemUpdateStart(status: UpdateStatus | undefined, confirm: (message: string) => boolean = window.confirm) {
  return confirm(getSystemUpdateConfirmationMessage(status));
}

function Pill({ children, tone = "slate" }: { children: React.ReactNode; tone?: PillTone }) {
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
  const [status, setStatus] = useState<UpdateStatus | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [isUpdating, setIsUpdating] = useState(false);
  const [isRollingBack, setIsRollingBack] = useState(false);
  const [isRestarting, setIsRestarting] = useState(false);
  const [needsRestart, setNeedsRestart] = useState(false);
  const [lastMessage, setLastMessage] = useState("");

  const statusValue = status ?? undefined;
  const releaseSyncState = getReleaseSyncState(statusValue);
  const latestVersion = getLatestVersion(statusValue);
  const releaseUrl = getReleaseUrl(statusValue);
  const manualGuidance = getManualUpdateGuidance(statusValue);
  const systemdReleaseMode = isSystemdReleaseMode(statusValue);
  const updateActions = getUpdateActionAvailability(statusValue, { busy: isUpdating || isRollingBack || isRestarting, needsRestart });
  const { canUpdate, canRollback, webUpdateMode } = updateActions;
  const hasUpdate = hasAvailableSystemUpdate(statusValue);
  const busy = isUpdating || isRollingBack || isRestarting;

  const releaseNotes = useMemo(() => String(status?.release_info?.body ?? "").trim(), [status?.release_info?.body]);
  const releaseAssets = status?.release_info?.assets ?? [];

  const actionHint = useMemo(
    () => getUpdateActionHint(statusValue, { busy, needsRestart }),
    [busy, needsRestart, statusValue],
  );

  const loadStatus = async (force = false) => {
    if (force) {
      setIsRefreshing(true);
    } else {
      setIsLoading(true);
    }
    try {
      const next = await checkSystemUpdates(force);
      setStatus(next);
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

  const handleUpdate = async () => {
    if (!canUpdate) return;
    if (!confirmSystemUpdateStart(statusValue)) return;
    setIsUpdating(true);
    try {
      const result = await performSystemUpdate();
      const message = result.message || "更新完成";
      setLastMessage(message);
      setNeedsRestart(Boolean(result.need_restart));
      toast.success(message);
      if (!result.need_restart) {
        await loadStatus(true);
      }
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "系统更新失败");
    } finally {
      setIsUpdating(false);
    }
  };

  const handleRollback = async () => {
    if (!canRollback) return;
    if (!window.confirm("回滚会恢复上一个发布包，完成后可能需要重启服务。确认继续？")) return;
    setIsRollingBack(true);
    try {
      const result = await rollbackSystemUpdate();
      const message = result.message || "回滚完成";
      setLastMessage(message);
      setNeedsRestart(Boolean(result.need_restart));
      toast.success(message);
      if (!result.need_restart) {
        await loadStatus(true);
      }
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "回滚失败");
    } finally {
      setIsRollingBack(false);
    }
  };

  const handleRestart = async () => {
    if (isRestarting) return;
    setIsRestarting(true);
    try {
      const result = await restartSystemService();
      setLastMessage(result.message || "服务重启已发起");
      setNeedsRestart(false);
      toast.success(result.message || "服务重启已发起");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "重启服务失败");
    } finally {
      setIsRestarting(false);
    }
  };

  if (isLoading && !status) {
    return (
      <DataPanel title="版本更新中心" description="正在检查当前部署版本和 GitHub Release 状态。">
        <div className="p-5">
          <EmptyState title="正在加载更新状态" description="同步版本、发布信息和部署模式。 " icon={<LoaderCircle className="size-7 animate-spin" />} />
        </div>
      </DataPanel>
    );
  }

  return (
    <DataPanel
      title="版本更新中心"
      description="面向 Genapi 0.1.12 的系统更新入口；systemd release 和受支持的容器部署可网页更新，其余部署显示手动步骤。"
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
          {webUpdateMode ? (
            <Button className="h-9 rounded-xl bg-stone-950 px-4 text-white hover:bg-stone-800" disabled={!canUpdate} onClick={() => void handleUpdate()}>
              {isUpdating ? <LoaderCircle className="size-4 animate-spin" /> : <Rocket className="size-4" />}
              更新系统
            </Button>
          ) : null}
          {needsRestart ? (
            <Button className="h-9 rounded-xl bg-emerald-700 px-4 text-white hover:bg-emerald-600" disabled={isRestarting} onClick={() => void handleRestart()}>
              {isRestarting ? <LoaderCircle className="size-4 animate-spin" /> : <Power className="size-4" />}
              重启服务
            </Button>
          ) : null}
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
            <div className="mt-2 flex flex-wrap items-center gap-2 text-2xl font-black text-stone-950">
              {latestVersion || "未检测"}
              <Pill tone={releaseSyncState.tone}>{releaseSyncState.label}</Pill>
            </div>
          </div>
          <div className="rounded-2xl border border-stone-200 bg-white p-4">
            <div className="text-xs font-bold text-stone-500">运行模式</div>
            <div className="mt-2 flex flex-wrap items-center gap-2">
              <Pill tone={webUpdateMode ? "emerald" : "amber"}>{getDeploymentModeLabel(statusValue)}</Pill>
              <Pill>{status?.build_type ?? "source"}</Pill>
              <Pill tone={webUpdateMode ? "emerald" : "amber"}>{webUpdateMode ? "可网页更新" : "手动更新"}</Pill>
            </div>
          </div>
        </div>

        {status?.error ? (
          <div className="flex gap-2 rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm leading-6 text-rose-800">
            <AlertTriangle className="mt-0.5 size-4 shrink-0" />
            <span>{status.error}</span>
          </div>
        ) : null}

        {status?.warning ? (
          <div className="flex gap-2 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm leading-6 text-amber-800">
            <AlertTriangle className="mt-0.5 size-4 shrink-0" />
            <span>{status.warning}</span>
          </div>
        ) : null}

        {actionHint && !status?.error && !status?.warning ? (
          <div className="rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm leading-6 text-amber-800">{actionHint}</div>
        ) : null}

        {manualGuidance ? (
          <div className="rounded-2xl border border-stone-200 bg-stone-50/80 p-4">
            <div className="flex items-center gap-2 text-sm font-black text-stone-950">
              <Terminal className="size-4 text-stone-500" />
              {manualGuidance.title}
            </div>
            <p className="mt-2 text-sm leading-6 text-stone-600">{manualGuidance.description}</p>
            <div className="mt-3 rounded-xl bg-slate-950 px-3 py-2 font-mono text-xs leading-6 text-slate-100">{manualGuidance.command}</div>
          </div>
        ) : null}

        <div className="grid gap-4 xl:grid-cols-[1.05fr_0.95fr]">
          <div className="space-y-3 rounded-2xl border border-stone-200 bg-white p-4">
            <div className="flex items-center justify-between gap-3">
              <div>
                <h3 className="flex items-center gap-2 text-sm font-black text-stone-950">
                  <FileText className="size-4 text-stone-500" />
                  Release 说明
                </h3>
                {status?.release_info?.published_at ? <p className="mt-1 text-xs text-stone-500">发布时间：{formatDate(status.release_info.published_at)}</p> : null}
              </div>
              {releaseUrl ? (
                <Button asChild variant="outline" className="h-9 rounded-xl border-stone-200 bg-white text-stone-700">
                  <a href={releaseUrl} target="_blank" rel="noreferrer">
                    查看 Release
                    <ExternalLink className="size-4" />
                  </a>
                </Button>
              ) : null}
            </div>

            {releaseNotes ? (
              <div className="max-h-64 overflow-auto whitespace-pre-wrap rounded-xl border border-stone-200 bg-stone-50 px-4 py-3 text-sm leading-6 text-stone-700">
                {releaseNotes}
              </div>
            ) : (
              <EmptyState title="暂无 Release 说明" description="刷新后会显示 GitHub Release 链接和发布说明。" />
            )}

            {releaseAssets.length ? (
              <div className="flex flex-wrap gap-2">
                {releaseAssets.map((asset) => (
                  <Pill key={`${asset.name}-${asset.size ?? 0}`}>{asset.name}</Pill>
                ))}
              </div>
            ) : null}
          </div>

          <div className="space-y-4 rounded-2xl border border-stone-200 bg-stone-950 p-4 text-white">
            <div className="flex items-center justify-between gap-3">
              <div>
                <h3 className="text-sm font-black">更新操作</h3>
                <p className="mt-1 text-xs text-stone-400">发布包更新完成后会提示重启服务。</p>
              </div>
              <Pill tone={webUpdateMode ? "emerald" : "amber"}>{webUpdateMode ? "网页更新" : "手动更新"}</Pill>
            </div>

            {webUpdateMode ? (
              <div className={`grid gap-2 ${systemdReleaseMode ? "sm:grid-cols-2" : ""}`}>
                <Button className="h-10 rounded-xl bg-white text-stone-950 hover:bg-stone-100" disabled={!canUpdate} onClick={() => void handleUpdate()}>
                  {isUpdating ? <LoaderCircle className="size-4 animate-spin" /> : <Rocket className="size-4" />}
                  更新系统
                </Button>
                {systemdReleaseMode ? (
                  <Button
                    variant="outline"
                    className="h-10 rounded-xl border-white/20 bg-white/10 text-white hover:bg-white/15"
                    disabled={!canRollback}
                    onClick={() => void handleRollback()}
                  >
                    {isRollingBack ? <LoaderCircle className="size-4 animate-spin" /> : <RotateCcw className="size-4" />}
                    回滚
                  </Button>
                ) : null}
              </div>
            ) : manualGuidance ? (
              <div className="space-y-2">
                <p className="text-sm leading-6 text-stone-300">{manualGuidance.description}</p>
                <div className="rounded-xl bg-white/10 px-3 py-2 font-mono text-xs leading-6 text-stone-100">{manualGuidance.command}</div>
              </div>
            ) : null}

            {needsRestart ? (
              <Button className="h-10 w-full rounded-xl bg-emerald-600 text-white hover:bg-emerald-500" disabled={isRestarting} onClick={() => void handleRestart()}>
                {isRestarting ? <LoaderCircle className="size-4 animate-spin" /> : <Power className="size-4" />}
                重启服务
              </Button>
            ) : webUpdateMode ? (
              <div className="rounded-xl border border-white/10 bg-white/5 px-3 py-2 text-sm leading-6 text-stone-300">更新或回滚完成后，这里会显示重启服务按钮。</div>
            ) : null}

            {lastMessage ? (
              <div className="flex gap-2 rounded-xl border border-emerald-400/30 bg-emerald-400/10 px-3 py-2 text-sm leading-6 text-emerald-100">
                <CheckCircle2 className="mt-0.5 size-4 shrink-0" />
                <span>{lastMessage}</span>
              </div>
            ) : null}
          </div>
        </div>
      </div>
    </DataPanel>
  );
}
