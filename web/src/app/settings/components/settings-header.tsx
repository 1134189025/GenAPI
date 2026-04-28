"use client";

import { PageHeader } from "@/components/common/page-header";

export function SettingsHeader() {
  return (
    <PageHeader
      eyebrow="Settings"
      title="设置"
      description="集中配置系统运行参数、注册认证、CPA 与 Sub2API 导入源。"
    />
  );
}
