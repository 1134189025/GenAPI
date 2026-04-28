import { describe, expect, test } from "bun:test";
import { readFileSync } from "node:fs";
import { join } from "node:path";

const root = join(import.meta.dir, "..");

function source(path: string) {
  return readFileSync(join(root, path), "utf8");
}

describe("image queue and build safety", () => {
  test("processes pending image requests sequentially instead of parallelizing a turn", () => {
    const page = source("src/app/image/page.tsx");

    expect(page).not.toContain("const tasks = pendingImages.map");
    expect(page).not.toContain("Promise.allSettled(tasks)");
    expect(page).toContain("for (const pendingImage of pendingImages)");
  });

  test("drains queued conversations through one global image queue worker", () => {
    const page = source("src/app/image/page.tsx");

    expect(page).toContain("let imageQueueDrainInProgress = false");
    expect(page).toContain("const drainConversationQueues = useCallback");
    expect(page).not.toContain("for (const conversation of conversationsRef.current)");
    expect(page).not.toContain("for (const conversation of conversations) {");
  });

  test("does not resurrect a deleted image conversation from a stale queue snapshot", () => {
    const page = source("src/app/image/page.tsx");

    expect(page).not.toContain("const conversation = current ?? snapshot");
  });

  test("guards the image page before scoped storage receives an empty auth subject id", () => {
    const page = source("src/app/image/page.tsx");

    expect(page).toContain('const userId = String(session.subjectId || "").trim()');
    expect(page).toContain("if (!userId)");
    expect(page).toContain("userId={userId}");
  });

  test("cancels queued image work when the authenticated user scope unmounts", () => {
    const page = source("src/app/image/page.tsx");

    expect(page).toContain("isImageQueueOwnerActiveRef");
    expect(page).toContain("isImageQueueOwnerActiveRef.current = false");
    expect(page).toContain("if (!isImageQueueOwnerActiveRef.current)");
  });

  test("stops queued image work when the stored auth session changes in another tab", () => {
    const page = source("src/app/image/page.tsx");
    const api = source("src/lib/api.ts");
    const auth = source("src/store/auth.ts");

    expect(auth).toContain("AUTH_SESSION_BROADCAST_CHANNEL");
    expect(auth).toContain("postMessage");
    expect(page).toContain("AUTH_SESSION_BROADCAST_CHANNEL");
    expect(page).toContain("getStoredAuthSession");
    expect(page).toContain("isCurrentImageQueueOwner");
    expect(page).toContain("sessionKey");
    expect(page).toContain("generateImage(queuedTurn.prompt, queuedTurn.model, queuedTurn.size, sessionKey)");
    expect(page).toContain("editImage(referenceFiles, queuedTurn.prompt, queuedTurn.model, queuedTurn.size, sessionKey)");
    expect(api).toContain("authToken?: string");
    expect(api).toContain("Authorization: `Bearer ${token}`");
  });

  test("does not let production builds ignore TypeScript errors", () => {
    const nextConfig = source("next.config.ts");

    expect(nextConfig).not.toContain("ignoreBuildErrors: true");
  });

  test("keeps the image page focused on consumer creation instead of dashboard management", () => {
    const page = source("src/app/image/page.tsx");
    const composer = source("src/app/image/components/image-composer.tsx");
    const results = source("src/app/image/components/image-results.tsx");
    const sidebar = source("src/app/image/components/image-sidebar.tsx");

    expect(page).not.toContain("Image Dashboard");
    expect(page).not.toContain("DashboardStatCard");
    expect(page).not.toContain("xl:grid-cols-[minmax(0,1fr)_360px]");
    expect(composer).not.toContain("Creation Panel");
    expect(composer).not.toContain("lg:grid-cols-[minmax(0,1fr)_320px]");
    expect(composer).toContain("更多设置");
    expect(results).toContain("想生成什么图片");
    expect(results).not.toContain("Result Feed");
    expect(results).not.toContain("SummaryTile");
    expect(results).not.toContain("Round {turnIndex + 1}");
    expect(results).not.toContain("本轮参考图");
    expect(sidebar).not.toContain("History Panel");
    expect(sidebar).not.toContain("HistoryMetric");
  });
});
