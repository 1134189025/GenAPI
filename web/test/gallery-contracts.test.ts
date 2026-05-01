import { beforeEach, describe, expect, mock, test } from "bun:test";
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";

const root = join(import.meta.dir, "..");

const httpRequest = mock(async (path: string) => {
  if (path.startsWith("/api/gallery/images")) {
    return { items: [], next_cursor: null };
  }
  return {};
});
const httpBlobRequest = mock(async () => new Blob(["image"], { type: "image/png" }));

mock.module("../src/lib/request", () => ({ httpRequest, httpBlobRequest }));
mock.module("@/lib/request", () => ({ httpRequest, httpBlobRequest }));

const storage = new Map<string, unknown>();
mock.module("localforage", () => ({
  default: {
    createInstance: () => ({
      getItem: async (key: string) => storage.get(key) ?? null,
      setItem: async (key: string, value: unknown) => {
        storage.set(key, value);
      },
      removeItem: async (key: string) => {
        storage.delete(key);
      },
    }),
  },
}));

const api = await import("../src/lib/api");

function source(path: string) {
  return readFileSync(join(root, path), "utf8");
}

describe("user gallery contracts", () => {
  beforeEach(() => {
    httpRequest.mockClear();
    httpBlobRequest.mockClear();
    storage.clear();
  });

  test("uses the user gallery API contracts and authenticated blob content fetches", async () => {
    expect(typeof api.fetchGalleryImages).toBe("function");
    expect(typeof api.deleteGalleryImage).toBe("function");
    expect(typeof api.fetchGalleryImageContentBlob).toBe("function");

    await api.fetchGalleryImages();
    await api.fetchGalleryImages("cursor 1");
    await api.fetchGalleryImages({ cursor: "cursor 1", limit: 24 });
    await api.fetchGalleryImages({ cursor: null, limit: 24 });
    await api.deleteGalleryImage("gallery-1");
    await api.fetchGalleryImageContentBlob("gallery-1");

    expect(httpRequest.mock.calls).toEqual([
      ["/api/gallery/images"],
      ["/api/gallery/images?cursor=cursor+1"],
      ["/api/gallery/images?cursor=cursor+1&limit=24"],
      ["/api/gallery/images?limit=24"],
      ["/api/gallery/images/gallery-1", { method: "DELETE" }],
    ]);
    expect(httpBlobRequest.mock.calls).toEqual([["/api/gallery/images/gallery-1/content"]]);
  });

  test("declares cursor pagination response fields and load-more gallery UI", () => {
    const apiSource = source("src/lib/api.ts");
    const galleryPage = source("src/app/gallery/page.tsx");

    expect(apiSource).toContain("next_cursor?: string | null");
    expect(apiSource).toContain("has_more?: boolean");
    expect(galleryPage).toContain("next_cursor");
    expect(galleryPage).toContain("has_more");
    expect(galleryPage).toContain("nextCursor");
    expect(galleryPage).toContain("hasMore");
    expect(galleryPage).toContain("加载更多");
    expect(galleryPage).toContain("图库加载失败");
    expect(galleryPage).toContain("正在整理图库");
    expect(galleryPage).toContain("fetchGalleryImages({ cursor, limit: 24 })");
  });

  test("loads gallery metadata before per-card blobs instead of all-or-nothing blob Promise.all", () => {
    const galleryPage = source("src/app/gallery/page.tsx");

    expect(galleryPage).toContain("loadPreviewForItem");
    expect(galleryPage).toContain("setFirstPageItems(data)");
    expect(galleryPage).not.toContain("Promise.all(");
    expect(galleryPage).not.toContain("data.items.map(async");
  });

  test("tracks per-card preview errors and appends deduped load-more items", () => {
    const galleryPage = source("src/app/gallery/page.tsx");

    expect(galleryPage).toContain("previewErrors");
    expect(galleryPage).toContain("setPreviewErrors");
    expect(galleryPage).toContain("loadMoreError");
    expect(galleryPage).toContain("appendDedupedGalleryItems");
    expect(galleryPage).toContain("newItems");
    expect(galleryPage).toContain("existingIds");
  });

  test("guards stale gallery list responses and deleted items during pagination races", () => {
    const galleryPage = source("src/app/gallery/page.tsx");

    expect(galleryPage).toContain("listRequestIdRef");
    expect(galleryPage).toContain("deletedItemIdsRef");
    expect(galleryPage).toContain("filterDeletedItems");
    expect(galleryPage).toContain("listRequestIdRef.current !== requestId");
    expect(galleryPage).toContain("deletedItemIdsRef.current.add(item.id)");
  });

  test("declares server gallery metadata in generated responses and stored image history", () => {
    const apiSource = source("src/lib/api.ts");
    const storeSource = source("src/store/image-conversations.ts");
    const imagePage = source("src/app/image/page.tsx");
    const results = source("src/app/image/components/image-results.tsx");

    expect(apiSource).toContain("gallery_id?: string");
    expect(apiSource).toContain("content_url?: string");
    expect(apiSource).toContain("expires_at?: string");
    expect(storeSource).toContain("serverId?: string");
    expect(storeSource).toContain("url?: string");
    expect(storeSource).toContain("expiresAt?: string");
    expect(imagePage).toContain("serverId");
    expect(imagePage).toContain("content_url");
    expect(imagePage).toContain("expiresAt");
    expect(results).toContain("image.url ||");
    expect(results).toContain("b64_json");
  });

  test("declares gallery resolution metadata and renders actual dimensions", () => {
    const apiSource = source("src/lib/api.ts");
    const galleryPage = source("src/app/gallery/page.tsx");

    expect(apiSource).toContain("width?: number");
    expect(apiSource).toContain("height?: number");
    expect(apiSource).toContain("target_size?: string");
    expect(apiSource).toContain("target_width?: number");
    expect(apiSource).toContain("target_height?: number");
    expect(galleryPage).toContain("galleryImageDimensions");
    expect(galleryPage).toContain("target_size");
    expect(galleryPage).toContain("实际尺寸");
  });

  test("scopes gallery edit handoff per user and consumes it once", async () => {
    const handoffPath = join(root, "src/store/image-edit-handoff.ts");
    expect(existsSync(handoffPath)).toBe(true);

    const handoff = await import("../src/store/image-edit-handoff");
    expect(handoff.getImageEditHandoffStorageKey("user a")).toBe("image_edit_handoff:user%20a");
    expect(() => handoff.getImageEditHandoffStorageKey("")).toThrow("owner id is required");

    await handoff.saveImageEditHandoff(
      "user-a",
      { name: "gallery-1.png", type: "image/png", dataUrl: "data:image/png;base64,aW1hZ2U=" },
      { galleryId: "gallery-1", prompt: "make it brighter", model: "gpt-image-2", size: "1:1" },
    );

    expect(await handoff.consumeImageEditHandoff("user-b")).toBeNull();

    const consumed = await handoff.consumeImageEditHandoff("user-a");
    expect(consumed?.image.name).toBe("gallery-1.png");
    expect(consumed?.source.galleryId).toBe("gallery-1");
    expect(await handoff.consumeImageEditHandoff("user-a")).toBeNull();
  });

  test("wires gallery continue-edit through a scoped handoff and downloads through blob content", () => {
    const galleryPagePath = join(root, "src/app/gallery/page.tsx");
    expect(existsSync(galleryPagePath)).toBe(true);

    const galleryPage = source("src/app/gallery/page.tsx");
    const imagePage = source("src/app/image/page.tsx");

    expect(galleryPage).toContain("fetchGalleryImageContentBlob(item.id)");
    expect(galleryPage).toContain("saveImageEditHandoff(");
    expect(galleryPage).toContain("userId,");
    expect(galleryPage).toContain('router.push("/image")');
    expect(galleryPage).toContain("URL.createObjectURL(blob)");
    expect(galleryPage).not.toContain("href={item.content_url}");
    expect(imagePage).toContain("consumeImageEditHandoff(userId)");
    expect(imagePage).toContain('setImageMode("edit")');
  });
});
