import { beforeEach, describe, expect, mock, test } from "bun:test";
import { readFileSync } from "node:fs";
import { join } from "node:path";

import {
  MAX_IMAGE_BATCH_CONCURRENCY,
  resolveImageBatchConcurrencyLimit,
  runBoundedImageBatch,
} from "../src/app/image/image-batch-runner.ts";
import { resolveStaticRequest } from "../scripts/serve-static.mjs";

const root = join(import.meta.dir, "..");
const storage = new Map<string, unknown>();
let imageConversationStoreImportCounter = 0;

mock.module("localforage", () => ({
  default: {
    createInstance: () => ({
      getItem: async (key: string) => storage.get(key) ?? null,
      setItem: async (key: string, value: unknown) => {
        storage.set(key, value);
        return value;
      },
      removeItem: async (key: string) => {
        storage.delete(key);
      },
    }),
  },
}));

function source(path: string) {
  return readFileSync(join(root, path), "utf8");
}

async function importImageConversationStore() {
  imageConversationStoreImportCounter += 1;
  return import(
    `../src/store/image-conversations.ts?image-conversation-store-${Date.now()}-${imageConversationStoreImportCounter}`
  );
}

async function waitForCondition(predicate: () => boolean, message: string) {
  for (let attempt = 0; attempt < 50; attempt += 1) {
    if (predicate()) {
      return;
    }
    await new Promise((resolve) => setTimeout(resolve, 0));
  }
  throw new Error(message);
}

beforeEach(() => {
  storage.clear();
});

describe("image queue and build safety", () => {
  test("processes pending image requests through a bounded concurrent turn runner", () => {
    const page = source("src/app/image/page.tsx");
    const batchRunner = source("src/app/image/image-batch-runner.ts");
    const api = source("src/lib/api.ts");

    expect(batchRunner).toContain("export const MAX_IMAGE_BATCH_CONCURRENCY = 10");
    expect(batchRunner).toContain("export function resolveImageBatchConcurrencyLimit");
    expect(batchRunner).toContain("export async function runBoundedImageBatch");
    expect(batchRunner).toContain("Promise.allSettled(workers)");
    expect(page).toContain("async function runPendingImagesWithConcurrency");
    expect(page).toContain("runBoundedImageBatch");
    expect(page).not.toContain("for (const pendingImage of pendingImages)");
    expect(page).not.toContain("const tasks = pendingImages.map");
    expect(batchRunner).toContain("const workerCount = Math.min(normalizedLimit, items.length)");
    expect(page).toContain("imageConcurrencyLimit");
    expect(api).toContain("n: 1");
    expect(api).toContain('formData.append("n", "1")');
  });

  test("bounded image batch helper runs in parallel without exceeding the limit", async () => {
    const items = Array.from({ length: 10 }, (_, index) => index);
    const releaseByItem = new Map<number, () => void>();
    const processed: number[] = [];
    let inFlight = 0;
    let maxInFlight = 0;

    const batch = runBoundedImageBatch({
      items,
      concurrencyLimit: 3,
      shouldContinue: () => true,
      runItem: async (item) => {
        inFlight += 1;
        maxInFlight = Math.max(maxInFlight, inFlight);
        await new Promise<void>((resolve) => {
          releaseByItem.set(item, resolve);
        });
        releaseByItem.delete(item);
        inFlight -= 1;
        processed.push(item);
      },
    });

    await waitForCondition(() => releaseByItem.size === 3, "first batch did not start concurrently");
    expect([...releaseByItem.keys()]).toEqual([0, 1, 2]);
    expect(maxInFlight).toBe(3);
    expect(processed).toEqual([]);

    releaseByItem.get(0)?.();
    await waitForCondition(() => releaseByItem.has(3), "worker did not refill after a slot completed");
    expect(inFlight).toBe(3);
    expect(maxInFlight).toBe(3);

    while (processed.length < items.length) {
      const releases = [...releaseByItem.values()];
      if (releases.length === 0) {
        await new Promise((resolve) => setTimeout(resolve, 0));
        continue;
      }
      releases.forEach((release) => release());
      await new Promise((resolve) => setTimeout(resolve, 0));
    }
    await batch;

    expect([...processed].sort((left, right) => left - right)).toEqual(items);
    expect(maxInFlight).toBe(3);
  });

  test("normalizes image batch concurrency to a safe range", () => {
    expect(MAX_IMAGE_BATCH_CONCURRENCY).toBe(10);
    expect(resolveImageBatchConcurrencyLimit(0)).toBe(1);
    expect(resolveImageBatchConcurrencyLimit(Number.NaN)).toBe(1);
    expect(resolveImageBatchConcurrencyLimit(4.8)).toBe(4);
    expect(resolveImageBatchConcurrencyLimit(99)).toBe(10);
  });

  test("drains queued conversations through a persistent image queue runtime", () => {
    const page = source("src/app/image/page.tsx");
    const runtime = source("src/app/image/image-queue-runtime.ts");

    expect(page).not.toContain("let imageQueueDrainInProgress = false");
    expect(page).toContain("getImageQueueRuntime(sessionKey)");
    expect(runtime).toContain("const imageQueueRuntimes = new Map<string, ImageQueueRuntime>()");
    expect(page).toContain("const drainConversationQueues = useCallback");
    expect(page).toContain("if (imageQueueRuntime.drainInProgress)");
    expect(page).toContain("imageQueueRuntime.drainInProgress = true");
    expect(page).toContain("imageQueueRuntime.drainInProgress = false");
    expect(page).not.toContain("for (const conversation of conversationsRef.current)");
    expect(page).not.toContain("for (const conversation of conversations) {");
  });

  test("shares active image queue ids across same-session image page instances", () => {
    const page = source("src/app/image/page.tsx");
    const runtime = source("src/app/image/image-queue-runtime.ts");

    expect(page).not.toContain("const activeConversationQueueIds = new Set<string>()");
    expect(runtime).toContain("activeConversationQueueIds: new Set<string>()");
    expect(page).toContain("activeConversationQueueIds.delete(conversationId)");
    expect(page).not.toContain("imageQueueRuntime.activeConversationQueueIds.clear()");
    expect(runtime).not.toContain("runtime.activeConversationQueueIds.clear()");
  });

  test("recovers interrupted queued image turns without leaving empty turns queued forever", () => {
    const page = source("src/app/image/page.tsx");

    expect(page).not.toContain('successCount > 0 ? "success" : "queued"');
    expect(page).toContain('successCount > 0 ? "success" : "error"');
    expect(page).not.toContain('existingSuccessCount > 0 ? "success" : "queued"');
    expect(page).toContain('existingSuccessCount > 0 ? "success" : "error"');
    expect(page).toContain('turn.error || "图片生成未完成"');
    expect(page).toContain('"图片生成未完成"');
    expect(page).toContain("const latestStoredUpdatedAt = Math.max");
    expect(page).toContain("const recoveryUpdatedAt = new Date(latestStoredUpdatedAt + 1).toISOString()");
    expect(page).toContain("updatedAt: recoveryUpdatedAt");
    expect(page).not.toContain("updatedAt: lastTurn?.createdAt || conversation.updatedAt");
  });

  test("labels a single queued image turn as waiting to start instead of background queued", () => {
    const results = source("src/app/image/components/image-results.tsx");

    expect(results).not.toContain(">排队中<");
    expect(results).toContain("等待生成");
    expect(results).toContain("正在启动");
  });

  test("does not resurrect a deleted image conversation from a stale queue snapshot", () => {
    const page = source("src/app/image/page.tsx");

    expect(page).not.toContain("const conversation = current ?? snapshot");
  });

  test("image conversation storage ignores stale same-owner saves after deletion", async () => {
    const store = await importImageConversationStore();
    const conversation = {
      id: "conversation-a",
      title: "Stale queue",
      createdAt: "2026-05-02T00:00:00.000Z",
      updatedAt: "2026-05-02T00:00:00.000Z",
      turns: [
        {
          id: "turn-a",
          prompt: "draw a house",
          model: "gpt-image-2" as const,
          mode: "generate" as const,
          referenceImages: [],
          count: 1,
          size: "1024x1024",
          images: [{ id: "turn-a-1", status: "loading" as const }],
          createdAt: "2026-05-02T00:00:00.000Z",
          status: "generating" as const,
        },
      ],
    };

    await store.saveImageConversation("user-a", conversation);
    await store.deleteImageConversation("user-a", conversation.id);
    const staleConversation = {
      ...conversation,
      updatedAt: "2026-05-02T00:01:00.000Z",
      turns: [
        {
          ...conversation.turns[0],
          status: "success" as const,
          images: [{ id: "turn-a-1", status: "success" as const, b64_json: "image-data" }],
        },
      ],
    };

    await store.saveImageConversations("user-a", [staleConversation]);
    expect(await store.listImageConversations("user-a")).toEqual([]);

    await store.saveImageConversation("user-a", staleConversation);
    expect(await store.listImageConversations("user-a")).toEqual([]);
  });

  test("image conversation storage deletion tombstones stay scoped to the owner and id", async () => {
    const store = await importImageConversationStore();
    const conversation = {
      id: "conversation-a",
      title: "Scoped tombstone",
      createdAt: "2026-05-02T00:00:00.000Z",
      updatedAt: "2026-05-02T00:00:00.000Z",
      turns: [
        {
          id: "turn-a",
          prompt: "draw a house",
          model: "gpt-image-2" as const,
          mode: "generate" as const,
          referenceImages: [],
          count: 1,
          size: "1024x1024",
          images: [{ id: "turn-a-1", status: "loading" as const }],
          createdAt: "2026-05-02T00:00:00.000Z",
          status: "generating" as const,
        },
      ],
    };

    await store.saveImageConversation("user-a", conversation);
    await store.deleteImageConversation("user-a", conversation.id);
    await store.saveImageConversation("user-a", { ...conversation, id: "conversation-b" });
    await store.saveImageConversation("user-b", conversation);

    expect((await store.listImageConversations("user-a")).map((item) => item.id)).toEqual(["conversation-b"]);
    expect((await store.listImageConversations("user-b")).map((item) => item.id)).toEqual(["conversation-a"]);
  });

  test("guards the image page before scoped storage receives an empty auth subject id", () => {
    const page = source("src/app/image/page.tsx");

    expect(page).toContain('const userId = String(session.subjectId || "").trim()');
    expect(page).toContain("if (!userId)");
    expect(page).toContain("userId={userId}");
  });

  test("keeps queued image work alive during normal route unmounts", () => {
    const page = source("src/app/image/page.tsx");
    const runtime = source("src/app/image/image-queue-runtime.ts");

    const ownerEffect = page.slice(page.indexOf("imageQueueRuntime.sessionKey = sessionKey"), page.indexOf("const loadHistory = async () => {"));
    expect(ownerEffect).toContain("deactivateImageQueueOwner(sessionKey)");
    expect(ownerEffect).toContain("channel.onmessage");
    expect(ownerEffect).not.toContain("return () => {\n      deactivateImageQueueOwner(sessionKey)");
    expect(page).toContain("hasLiveImageQueueWork(sessionKey)");
    expect(runtime).toContain('turn.status === "queued" || turn.status === "generating"');
    expect(runtime).toContain("runtime.drainInProgress");
    expect(runtime).toContain("IMAGE_QUEUE_UPDATED_EVENT");
  });

  test("stops queued image work when the stored auth session changes in another tab", () => {
    const page = source("src/app/image/page.tsx");
    const api = source("src/lib/api.ts");
    const auth = source("src/store/auth.ts");
    const shell = source("src/components/layout/app-shell.tsx");

    expect(auth).toContain("AUTH_SESSION_BROADCAST_CHANNEL");
    expect(auth).toContain("postMessage");
    expect(page).toContain("AUTH_SESSION_BROADCAST_CHANNEL");
    expect(page).toContain("getStoredAuthSession");
    expect(page).toContain("isCurrentImageQueueOwner");
    expect(page).toContain("sessionKey");
    expect(page).toContain("abortAllImageConversationRequests()");
    expect(page).toContain("generateImage(");
    expect(page).toContain("abortController.signal");
    expect(page).toContain(
      "editImage(",
    );
    expect(api).toContain("authToken?: string");
    expect(api).toContain("Authorization: `Bearer ${token}`");
    expect(shell).toContain("deactivateAllImageQueueRuntimes()");
    expect(shell).toContain("deactivateStaleImageQueueRuntimes(session.key)");
    expect(shell).toContain("new BroadcastChannel(AUTH_SESSION_BROADCAST_CHANNEL)");
    expect(shell).toContain("deactivateStaleImageQueueRuntimes(storedSession.key)");
  });

  test("aborts deleted in-flight image requests so visible queue work can continue", () => {
    const page = source("src/app/image/page.tsx");
    const api = source("src/lib/api.ts");
    const request = source("src/lib/request.ts");
    const deleteHandler = page.slice(page.indexOf("const handleDeleteConversation = async"), page.indexOf("const handleClearHistory = async"));

    expect(request).toContain("signal?: AbortSignal");
    expect(request).toContain("signal,");
    expect(api).toContain("signal?: AbortSignal");
    expect(api).toContain("signal,");
    expect(page).toContain("const abortImageConversationRequest = useCallback");
    expect(page).toContain("abortImageConversationRequest(id)");
    expect(page).toContain("const abortAllImageConversationRequests = useCallback");
    expect(page).toContain("abortAllImageConversationRequests()");
    expect(page).toContain("imageQueueRuntime.abortControllers.set(conversationId, abortController)");
    expect(page).toContain("abortController.signal.aborted");
    expect(page).toContain("markCurrentQueuedTurnCancelled");
    expect(page).toContain("imageQueueRuntime.abortControllers.delete(conversationId)");
    expect(deleteHandler).toContain("const previousConversations = imageQueueRuntime.conversations");
    expect(deleteHandler).toContain("const nextConversations = previousConversations.filter");
    expect(deleteHandler).not.toContain("const nextConversations = conversations.filter");
    expect(deleteHandler).not.toContain("await listImageConversations(userId)");
  });

  test("clears visible image queues before awaiting history deletion", () => {
    const page = source("src/app/image/page.tsx");
    const clearHistory = page.slice(page.indexOf("const handleClearHistory = async () => {"), page.indexOf("const openDeleteConversationConfirm"));

    expect(clearHistory).toContain("const previousConversations = imageQueueRuntime.conversations");
    expect(clearHistory).toContain("abortAllImageConversationRequests()");
    expect(clearHistory).toContain("conversationsRef.current = []");
    expect(clearHistory).toContain("setConversations([])");
    expect(clearHistory).toContain("await clearImageConversations(userId)");
    expect(clearHistory.indexOf("conversationsRef.current = []")).toBeLessThan(clearHistory.indexOf("await clearImageConversations(userId)"));
    expect(clearHistory.indexOf("setConversations([])")).toBeLessThan(clearHistory.indexOf("await clearImageConversations(userId)"));
    expect(clearHistory).toContain("markAbortedImageQueueRunsFailed");
    expect(clearHistory).toContain("setConversations(restoredConversations)");
  });

  test("does not finalize aborted image turns as successful loading results", () => {
    const page = source("src/app/image/page.tsx");
    const queueRunner = page.slice(page.indexOf("const runConversationQueue = useCallback"), page.indexOf("const drainConversationQueues = useCallback"));

    expect(page).toContain("function markAbortedImageQueueRunsFailed");
    expect(page).toContain("删除失败，已取消未完成的图片请求");
    expect(page).toContain("清空失败，已取消未完成的图片请求");
    expect(queueRunner).toContain("const markCurrentQueuedTurnCancelled = async () =>");
    expect(queueRunner).toContain('error: "图片请求已取消"');
    expect(queueRunner).toContain("if (abortController.signal.aborted)");
    expect(queueRunner.indexOf("await markCurrentQueuedTurnCancelled()")).toBeGreaterThan(queueRunner.indexOf("await runPendingImagesWithConcurrency"));
    expect(queueRunner.indexOf("await markCurrentQueuedTurnCancelled()")).toBeLessThan(queueRunner.indexOf("const latestTurn = current.turns.find"));
    expect(queueRunner).toContain("targetImage?.status !== \"loading\"");
    expect(queueRunner).toContain('status: "error" as const, error: "图片生成未完成"');
  });

  test("image composer exposes fixed resolution presets instead of free-form ratios", () => {
    const page = source("src/app/image/page.tsx");
    const composer = source("src/app/image/components/image-composer.tsx");
    const results = source("src/app/image/components/image-results.tsx");

    expect(composer).toContain("1024x1024");
    expect(composer).toContain("1536x864");
    expect(composer).toContain("864x1536");
    expect(composer).toContain("1280x960");
    expect(composer).toContain("960x1280");
    expect(composer).toContain("1920x1080");
    expect(composer).toContain("1080x1920");
    expect(composer).toContain("2560x1440");
    expect(composer).toContain("1440x2560");
    expect(composer).toContain("3840x2160");
    expect(composer).toContain("2160x3840");
    expect(page).toContain("1920x1080");
    expect(page).toContain("3840x2160");
    expect(results).toContain('"1920x1080"');
    expect(results).toContain('"2560x1440"');
    expect(results).toContain('"3840x2160"');
    expect(results).toContain('"1080x1920"');
    expect(results).toContain('"1440x2560"');
    expect(results).toContain('"2160x3840"');
    expect(composer).toContain(">尺寸<");
    expect(composer).not.toContain(">比例<");
  });

  test("image results expose target and actual resolution metadata", () => {
    const api = source("src/lib/api.ts");
    const store = source("src/store/image-conversations.ts");
    const results = source("src/app/image/components/image-results.tsx");

    expect(api).toContain("width?: number");
    expect(api).toContain("target_size?: string");
    expect(store).toContain("width?: number");
    expect(store).toContain("targetSize?: string");
    expect(results).toContain("targetSize");
    expect(results).toContain("actualDimensions");
  });

  test("does not let production builds ignore TypeScript errors", () => {
    const nextConfig = source("next.config.ts");

    expect(nextConfig).not.toContain("ignoreBuildErrors: true");
  });

  test("serves static export output instead of using incompatible next start", () => {
    const packageJson = JSON.parse(source("package.json")) as {
      scripts: Record<string, string>;
    };

    expect(packageJson.scripts.start).not.toContain("next start");
    expect(packageJson.scripts.start).toContain("scripts/serve-static.mjs");
    expect(typeof resolveStaticRequest).toBe("function");
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
