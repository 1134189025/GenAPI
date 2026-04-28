"use client";

import { useEffect, useRef } from "react";
import { LoaderCircle, PlugZap, ServerCog, Settings2, ShieldCheck } from "lucide-react";

import { StatCard } from "@/components/common/stat-card";
import { useAuthGuard } from "@/lib/use-auth-guard";

import { AuthSettingsCard } from "./components/auth-settings-card";
import { ConfigCard } from "./components/config-card";
import { CPAPoolDialog } from "./components/cpa-pool-dialog";
import { CPAPoolsCard } from "./components/cpa-pools-card";
import { ImportBrowserDialog } from "./components/import-browser-dialog";
import { SettingsHeader } from "./components/settings-header";
import { Sub2APIConnections } from "./components/sub2api-connections";
import { useSettingsStore } from "./store";

function SettingsDataController() {
  const didLoadRef = useRef(false);
  const initialize = useSettingsStore((state) => state.initialize);
  const loadPools = useSettingsStore((state) => state.loadPools);
  const pools = useSettingsStore((state) => state.pools);

  useEffect(() => {
    if (didLoadRef.current) {
      return;
    }
    didLoadRef.current = true;
    void initialize();
  }, [initialize]);

  useEffect(() => {
    const hasRunningJobs = pools.some((pool) => {
      const status = pool.import_job?.status;
      return status === "pending" || status === "running";
    });
    if (!hasRunningJobs) {
      return;
    }

    const timer = window.setInterval(() => {
      void loadPools(true);
    }, 1500);
    return () => window.clearInterval(timer);
  }, [loadPools, pools]);

  return null;
}

function SettingsOverviewStats() {
  const config = useSettingsStore((state) => state.config);
  const pools = useSettingsStore((state) => state.pools);

  return (
    <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
      <StatCard
        label="账号刷新"
        value={`${config?.refresh_account_interval_minute ?? "-"} min`}
        hint="自动刷新间隔"
        icon={<Settings2 className="size-5" />}
        tone="blue"
      />
      <StatCard
        label="图片保留"
        value={`${config?.image_retention_days ?? "-"} 天`}
        hint="本地归档清理周期"
        icon={<ShieldCheck className="size-5" />}
        tone="emerald"
      />
      <StatCard
        label="CPA 连接"
        value={pools.length}
        hint="可同步的远程连接"
        icon={<ServerCog className="size-5" />}
        tone="slate"
      />
      <StatCard
        label="全局代理"
        value={String(config?.proxy || "").trim() ? "已配置" : "未配置"}
        hint="出站请求代理"
        icon={<PlugZap className="size-5" />}
        tone={String(config?.proxy || "").trim() ? "emerald" : "slate"}
      />
    </div>
  );
}

function SettingsPageContent() {
  return (
    <>
      <SettingsDataController />
      <SettingsHeader />
      <SettingsOverviewStats />
      <section className="space-y-6">
        <ConfigCard />
        <AuthSettingsCard />
        <CPAPoolsCard />
        <Sub2APIConnections />
      </section>
      <CPAPoolDialog />
      <ImportBrowserDialog />
    </>
  );
}

export default function SettingsPage() {
  const { isCheckingAuth, session } = useAuthGuard(["admin"]);

  if (isCheckingAuth || !session || session.role !== "admin") {
    return (
      <div className="flex min-h-[40vh] items-center justify-center">
        <LoaderCircle className="size-5 animate-spin text-stone-400" />
      </div>
    );
  }

  return <SettingsPageContent />;
}
