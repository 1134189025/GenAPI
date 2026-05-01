import { beforeEach, describe, expect, mock, test } from "bun:test";
import { readFileSync } from "node:fs";
import { join } from "node:path";

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

beforeEach(() => {
  storage.clear();
});

describe("image queue and build safety", () => {
  test("processes pending image requests sequentially instead of parallelizing a turn", () => {
    const page = source("src/app/image/page.tsx");

    expect(page).not.toContain("const tasks = pendingImages.map");
    expect(page).not.toContain("Promise.allSettled(tasks)");
    expect(page).toContain("for (const pendingImage of pendingImages)");
  });

  test("drains queued conversations through a component instance image queue worker", () => {
    const page = source("src/app/image/page.tsx");

    expect(page).not.toContain("let imageQueueDrainInProgress = false");
    expect(page).toContain("const imageQueueDrainInProgressRef = useRef(false)");
    expect(page).toContain("const drainConversationQueues = useCallback");
    expect(page).toContain("if (imageQueueDrainInProgressRef.current)");
    expect(page).toContain("imageQueueDrainInProgressRef.current = true");
    expect(page).toContain("imageQueueDrainInProgressRef.current = false");
    expect(page).not.toContain("for (const conversation of conversationsRef.current)");
    expect(page).not.toContain("for (const conversation of conversations) {");
  });

  test("does not share active image queue ids across image page instances", () => {
    const page = source("src/app/image/page.tsx");

    expect(page).not.toContain("const activeConversationQueueIds = new Set<string>()");
    expect(page).toContain("const activeConversationQueueIdsRef = useRef<Set<string>>(new Set())");
    expect(page).toContain("activeConversationQueueIdsRef.current.clear()");
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
    expect(page).toContain(
      "editImage(referenceFiles, queuedTurn.prompt, queuedTurn.model, queuedTurn.size, sessionKey)",
    );
    expect(api).toContain("authToken?: string");
    expect(api).toContain("Authorization: `Bearer ${token}`");
  });

  test("image composer exposes fixed resolution presets instead of free-form ratios", () => {
    const composer = source("src/app/image/components/image-composer.tsx");

    expect(composer).toContain("1024x1024");
    expect(composer).toContain("1536x864");
    expect(composer).toContain("864x1536");
    expect(composer).toContain("1280x960");
    expect(composer).toContain("960x1280");
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
