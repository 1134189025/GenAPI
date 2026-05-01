import { describe, expect, test } from "bun:test";

import {
  confirmSystemUpdateStart,
  getManualUpdateGuidance,
  getReleaseSyncState,
  getUpdateActionHint,
  getUpdateActionAvailability,
} from "../src/app/settings/components/update-card";
import { isSub2APIAuthModeChanged, validateSub2APIServerForm } from "../src/app/settings/components/sub2api-connections";
import type { Sub2APIServer, UpdateStatus } from "../src/lib/api";

describe("settings UI helper contracts", () => {
  const passwordServer: Sub2APIServer = {
    id: "server-a",
    name: "Server A",
    base_url: "http://sub2api.local",
    email: "admin@example.test",
    has_api_key: false,
    group_id: "",
  };

  test("Sub2API edit requires a new secret when auth mode changes", () => {
    expect(isSub2APIAuthModeChanged(passwordServer, "api_key")).toBe(true);
    expect(
      validateSub2APIServerForm({
        editingServer: passwordServer,
        baseUrl: "http://sub2api.local",
        email: "",
        password: "",
        apiKey: "",
        authMode: "api_key",
      }),
    ).toBe("切换认证方式后请输入 Admin API Key");
    expect(
      validateSub2APIServerForm({
        editingServer: { ...passwordServer, has_api_key: true },
        baseUrl: "http://sub2api.local",
        email: "admin@example.test",
        password: "",
        apiKey: "",
        authMode: "password",
      }),
    ).toBe("切换认证方式后请输入管理员密码");
  });

  test("update card only displays synced when latest version is known and no update is available", () => {
    const baseStatus: UpdateStatus = {
      deployment_mode: "systemd",
      build_type: "release",
      can_update: true,
      has_update: false,
      current_version: "0.1.4",
    };

    expect(getReleaseSyncState(undefined).label).toBe("未检测");
    expect(getReleaseSyncState({ ...baseStatus, error: "network" }).label).toBe("检查失败");
    expect(getReleaseSyncState({ ...baseStatus, latest_version: undefined }).label).toBe("未检测");
    expect(getReleaseSyncState({ ...baseStatus, latest_version: "0.1.4" }).label).toBe("已同步");
    expect(getReleaseSyncState({ ...baseStatus, has_update: true, latest_version: "0.1.5" }).label).toBe("可更新");
    expect(getReleaseSyncState({ ...baseStatus, update_available: true, latest_version: "0.1.5" }).label).toBe("可更新");
  });

  test("update card guides non-systemd deployments through manual updates", () => {
    const dockerStatus: UpdateStatus = {
      deployment_mode: "docker",
      build_type: "release",
      can_update: false,
      has_update: true,
      current_version: "0.1.5",
      latest_version: "0.1.6",
    };
    const sourceStatus: UpdateStatus = {
      deployment_mode: "source",
      build_type: "source",
      can_update: false,
      has_update: true,
      current_version: "0.1.5",
      latest_version: "0.1.6",
    };
    const systemdStatus: UpdateStatus = {
      deployment_mode: "systemd",
      build_type: "release",
      can_update: true,
      has_update: true,
      current_version: "0.1.5",
      latest_version: "0.1.6",
    };

    expect(getManualUpdateGuidance(dockerStatus)?.description).toContain("同步最新 docker-compose.yml");
    expect(getManualUpdateGuidance(dockerStatus)?.command).toBe("git pull && docker compose pull app && docker compose up -d app");
    expect(getManualUpdateGuidance(sourceStatus)?.command).toBe("git pull && restart service manually");
    expect(getManualUpdateGuidance(systemdStatus)).toBeNull();
  });

  test("docker deployment can start web update without enabling rollback", () => {
    const dockerStatus: UpdateStatus = {
      deployment_mode: "docker",
      build_type: "release",
      can_update: true,
      has_update: true,
      current_version: "0.1.5",
      latest_version: "0.1.6",
    };
    const dockerComposeStatus: UpdateStatus = {
      mode: "docker-compose",
      build_type: "release",
      can_update: true,
      update_available: true,
      current_version: "0.1.5",
      latest_version: "0.1.6",
    };

    expect(getUpdateActionAvailability(dockerStatus)).toEqual({
      canUpdate: true,
      canRollback: false,
      webUpdateMode: true,
    });
    expect(getManualUpdateGuidance(dockerStatus)).toBeNull();
    expect(getUpdateActionAvailability(dockerComposeStatus).canUpdate).toBe(true);
    expect(getUpdateActionAvailability({ ...dockerStatus, has_update: false, update_available: false }).canUpdate).toBe(false);
    expect(getUpdateActionAvailability({ ...dockerStatus, can_update: false }).canUpdate).toBe(false);
  });

  test("systemd release update and rollback behavior is unchanged", () => {
    const systemdStatus: UpdateStatus = {
      deployment_mode: "systemd",
      build_type: "release",
      has_update: true,
      current_version: "0.1.5",
      latest_version: "0.1.6",
    };

    expect(getUpdateActionAvailability(systemdStatus)).toEqual({
      canUpdate: true,
      canRollback: true,
      webUpdateMode: true,
    });
    expect(getUpdateActionAvailability({ ...systemdStatus, has_update: false }).canUpdate).toBe(false);
    expect(getUpdateActionAvailability({ ...systemdStatus, can_update: false })).toEqual({
      canUpdate: false,
      canRollback: false,
      webUpdateMode: true,
    });
  });

  test("docker preflight failures still show manual compose guidance", () => {
    const dockerStatus: UpdateStatus = {
      deployment_mode: "docker",
      build_type: "release",
      can_update: false,
      has_update: true,
      current_version: "0.1.5",
      latest_version: "0.1.6",
      disabled_reason: "docker socket is unavailable",
    };

    expect(getManualUpdateGuidance(dockerStatus)?.command).toBe("git pull && docker compose pull app && docker compose up -d app");
  });

  test("docker preflight action hint prioritizes compose sync guidance over raw errors", () => {
    const dockerStatus: UpdateStatus = {
      deployment_mode: "docker",
      build_type: "source",
      can_update: false,
      has_update: true,
      current_version: "0.1.10",
      latest_version: "0.1.11",
      disabled_reason: "Docker socket is not mounted: /var/run/docker.sock",
    };

    expect(getUpdateActionHint(dockerStatus)).toContain("同步最新 docker-compose.yml");
    expect(getUpdateActionHint(dockerStatus)).not.toContain("Docker socket is not mounted");
  });

  test("system update start requires explicit confirmation", () => {
    let confirmCalls = 0;
    const systemdStatus: UpdateStatus = {
      deployment_mode: "systemd",
      build_type: "release",
      can_update: true,
      has_update: true,
      current_version: "0.1.5",
      latest_version: "0.1.6",
    };
    const confirm = (message: string) => {
      confirmCalls += 1;
      expect(message).toBe("系统更新会下载并安装最新发布包，完成后可能需要重启服务。确认立即更新？");
      return false;
    };

    expect(confirmSystemUpdateStart(systemdStatus, confirm)).toBe(false);
    expect(confirmCalls).toBe(1);
  });

  test("docker system update confirmation warns about container recreation", () => {
    const dockerStatus: UpdateStatus = {
      deployment_mode: "docker",
      build_type: "release",
      can_update: true,
      update_available: true,
      current_version: "0.1.5",
      latest_version: "0.1.6",
    };
    const confirm = (message: string) => {
      expect(message).toContain("容器");
      expect(message).toContain("重新创建");
      expect(message).toContain("短暂断开");
      return false;
    };

    expect(confirmSystemUpdateStart(dockerStatus, confirm)).toBe(false);
  });
});
