"use client";

import { useEffect, useRef } from "react";
import { LoaderCircle } from "lucide-react";

import { RegisterCard } from "@/app/register/components/register-card";
import { useSettingsStore } from "@/app/settings/store";
import { PageHeader } from "@/components/common/page-header";
import { useAuthGuard } from "@/lib/use-auth-guard";

function RegisterDataController() {
  const didLoadRef = useRef(false);
  const loadRegister = useSettingsStore((state) => state.loadRegister);

  useEffect(() => {
    if (didLoadRef.current) return;
    didLoadRef.current = true;
    void loadRegister();
  }, [loadRegister]);

  useEffect(() => {
    const timer = window.setInterval(() => {
      void loadRegister(true);
    }, 1000);
    return () => {
      window.clearInterval(timer);
    };
  }, [loadRegister]);

  return null;
}

function RegisterMachineContent() {
  return (
    <>
      <RegisterDataController />
      <PageHeader
        eyebrow="Register Machine"
        title="ChatGPT注册机"
        description="配置批量注册、临时邮箱提供商、代理和导入策略，实时查看任务进度。"
      />
      <section>
        <RegisterCard />
      </section>
    </>
  );
}

export default function RegisterMachinePage() {
  const { isCheckingAuth, session } = useAuthGuard(["admin"]);

  if (isCheckingAuth || !session || session.role !== "admin") {
    return (
      <div className="flex min-h-[40vh] items-center justify-center">
        <LoaderCircle className="size-5 animate-spin text-stone-400" />
      </div>
    );
  }

  return <RegisterMachineContent />;
}
