"use client";
/* eslint-disable @next/next/no-img-element -- Gallery thumbnails are authenticated blob object URLs. */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { Download, ImageIcon, LoaderCircle, RefreshCw, Sparkles, Trash2 } from "lucide-react";
import { toast } from "sonner";

import { EmptyState } from "@/components/common/empty-state";
import { PageHeader } from "@/components/common/page-header";
import { ImageLightbox } from "@/components/image-lightbox";
import { Button } from "@/components/ui/button";
import {
  deleteGalleryImage,
  fetchGalleryImageContentBlob,
  fetchGalleryImages,
  type GalleryImage,
  type GalleryImagesResponse,
} from "@/lib/api";
import { useAuthGuard } from "@/lib/use-auth-guard";
import { cn } from "@/lib/utils";
import { saveImageEditHandoff } from "@/store/image-edit-handoff";

type BlobPreview = {
  url: string;
  blob: Blob;
};

function formatDate(value: string | null | undefined) {
  if (!value) return "--";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

function formatBytes(value: number | undefined) {
  const bytes = Math.max(0, Number(value || 0));
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function galleryImageSizeBytes(item: GalleryImage) {
  return Number(item.size_bytes ?? item.byte_size ?? 0);
}

function galleryImageDimensions(item: GalleryImage) {
  const width = Number(item.width);
  const height = Number(item.height);
  if (!Number.isFinite(width) || !Number.isFinite(height) || width <= 0 || height <= 0) {
    return "";
  }
  return `${width} x ${height}`;
}

function galleryTargetSize(item: GalleryImage) {
  return String(item.target_size || item.size || "").trim();
}

function blobToDataUrl(blob: Blob) {
  return new Promise<string>((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ""));
    reader.onerror = () => reject(new Error("读取图库图片失败"));
    reader.readAsDataURL(blob);
  });
}

function downloadBlob(blob: Blob, filename: string) {
  const objectUrl = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = objectUrl;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(objectUrl);
}

function galleryErrorMessage(error: unknown, fallback: string) {
  return error instanceof Error ? error.message : fallback;
}

function appendDedupedGalleryItems(currentItems: GalleryImage[], incomingItems: GalleryImage[]) {
  const existingIds = new Set(currentItems.map((item) => item.id));
  const newItems = incomingItems.filter((item) => {
    if (!item.id || existingIds.has(item.id)) return false;
    existingIds.add(item.id);
    return true;
  });
  return {
    items: [...currentItems, ...newItems],
    newItems,
  };
}

async function requestGalleryPage(cursor: string | null) {
  return fetchGalleryImages({ cursor, limit: 24 });
}

export default function GalleryPage() {
  const router = useRouter();
  const { isCheckingAuth, session } = useAuthGuard();
  const userId = String(session?.subjectId || "").trim();
  const sessionKey = String(session?.key || "").trim();
  const galleryGenerationRef = useRef(0);
  const listRequestIdRef = useRef(0);
  const deletedItemIdsRef = useRef<Set<string>>(new Set());
  const itemsRef = useRef<GalleryImage[]>([]);
  const itemIdsRef = useRef<Set<string>>(new Set());
  const previewUrlsRef = useRef<Record<string, BlobPreview>>({});
  const [items, setItems] = useState<GalleryImage[]>([]);
  const [previews, setPreviews] = useState<Record<string, BlobPreview>>({});
  const [previewErrors, setPreviewErrors] = useState<Record<string, string>>({});
  const [isInitialLoading, setIsInitialLoading] = useState(false);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [isLoadingMore, setIsLoadingMore] = useState(false);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [hasMore, setHasMore] = useState(false);
  const [metadataError, setMetadataError] = useState("");
  const [loadMoreError, setLoadMoreError] = useState("");
  const [busyIds, setBusyIds] = useState<Record<string, boolean>>({});
  const [lightboxOpen, setLightboxOpen] = useState(false);
  const [lightboxIndex, setLightboxIndex] = useState(0);

  const lightboxImages = useMemo(
    () =>
      items
        .map((item) => {
          const preview = previews[item.id];
          if (!preview) return null;
          return {
            id: item.id,
            src: preview.url,
            sizeLabel: formatBytes(galleryImageSizeBytes(item)),
            dimensions: galleryImageDimensions(item),
          };
        })
        .filter((item): item is { id: string; src: string; sizeLabel: string; dimensions: string } => Boolean(item)),
    [items, previews],
  );

  const setBusy = useCallback((id: string, busy: boolean) => {
    setBusyIds((current) => {
      const next = { ...current };
      if (busy) {
        next[id] = true;
      } else {
        delete next[id];
      }
      return next;
    });
  }, []);

  const syncItemRefs = useCallback((nextItems: GalleryImage[]) => {
    itemsRef.current = nextItems;
    itemIdsRef.current = new Set(nextItems.map((item) => item.id));
  }, []);

  const filterDeletedItems = useCallback(
    (incomingItems: GalleryImage[]) => incomingItems.filter((item) => !deletedItemIdsRef.current.has(item.id)),
    [],
  );

  const revokeAllPreviewUrls = useCallback(() => {
    for (const preview of Object.values(previewUrlsRef.current)) {
      URL.revokeObjectURL(preview.url);
    }
    previewUrlsRef.current = {};
  }, []);

  const trimPreviewScope = useCallback((nextItems: GalleryImage[]) => {
    const nextIds = new Set(nextItems.map((item) => item.id));
    setPreviews((current) => {
      const next: Record<string, BlobPreview> = {};
      for (const [id, preview] of Object.entries(current)) {
        if (nextIds.has(id)) {
          next[id] = preview;
        } else {
          URL.revokeObjectURL(preview.url);
        }
      }
      previewUrlsRef.current = next;
      return next;
    });
    setPreviewErrors((current) => {
      const next: Record<string, string> = {};
      for (const [id, message] of Object.entries(current)) {
        if (nextIds.has(id)) next[id] = message;
      }
      return next;
    });
  }, []);

  const clearGalleryState = useCallback(() => {
    galleryGenerationRef.current += 1;
    listRequestIdRef.current += 1;
    deletedItemIdsRef.current = new Set();
    syncItemRefs([]);
    revokeAllPreviewUrls();
    setItems([]);
    setPreviews({});
    setPreviewErrors({});
    setIsInitialLoading(false);
    setIsRefreshing(false);
    setIsLoadingMore(false);
    setNextCursor(null);
    setHasMore(false);
    setMetadataError("");
    setLoadMoreError("");
    setBusyIds({});
    setLightboxOpen(false);
    setLightboxIndex(0);
  }, [revokeAllPreviewUrls, syncItemRefs]);

  const setFirstPageItems = useCallback(
    (data: GalleryImagesResponse) => {
      const nextItems = filterDeletedItems(data.items);
      syncItemRefs(nextItems);
      setItems(nextItems);
      trimPreviewScope(nextItems);
    },
    [filterDeletedItems, syncItemRefs, trimPreviewScope],
  );

  const appendItems = useCallback(
    (incomingItems: GalleryImage[]) => {
      const result = appendDedupedGalleryItems(itemsRef.current, filterDeletedItems(incomingItems));
      syncItemRefs(result.items);
      setItems(result.items);
      return result.newItems;
    },
    [filterDeletedItems, syncItemRefs],
  );

  const applyPagination = useCallback((data: GalleryImagesResponse) => {
    setNextCursor(data.next_cursor ?? null);
    setHasMore(data.has_more ?? Boolean(data.next_cursor));
  }, []);

  const loadPreviewForItem = useCallback(async (item: GalleryImage, generation = galleryGenerationRef.current) => {
    if (!item.id || !itemIdsRef.current.has(item.id)) return;
    if (previewUrlsRef.current[item.id]) {
      setPreviewErrors((current) => {
        if (!(item.id in current)) return current;
        const next = { ...current };
        delete next[item.id];
        return next;
      });
      return;
    }

    setPreviewErrors((current) => {
      if (!(item.id in current)) return current;
      const next = { ...current };
      delete next[item.id];
      return next;
    });

    try {
      const blob = await fetchGalleryImageContentBlob(item.id);
      if (galleryGenerationRef.current !== generation || !itemIdsRef.current.has(item.id)) return;

      const url = URL.createObjectURL(blob);
      if (galleryGenerationRef.current !== generation || !itemIdsRef.current.has(item.id)) {
        URL.revokeObjectURL(url);
        return;
      }

      setPreviews((current) => {
        if (!itemIdsRef.current.has(item.id)) {
          URL.revokeObjectURL(url);
          return current;
        }
        if (current[item.id]) {
          URL.revokeObjectURL(url);
          return current;
        }
        const next = { ...current, [item.id]: { blob, url } };
        previewUrlsRef.current = next;
        return next;
      });
    } catch (previewError) {
      if (galleryGenerationRef.current !== generation || !itemIdsRef.current.has(item.id)) return;
      setPreviewErrors((current) => ({
        ...current,
        [item.id]: galleryErrorMessage(previewError, "预览加载失败"),
      }));
    }
  }, []);

  const loadFirstPage = useCallback(
    async (mode: "initial" | "refresh") => {
      if (!userId) return;
      const generation = galleryGenerationRef.current;
      const requestId = listRequestIdRef.current + 1;
      listRequestIdRef.current = requestId;
      if (mode === "initial") {
        setIsInitialLoading(true);
      } else {
        setIsRefreshing(true);
      }
      setIsLoadingMore(false);
      setMetadataError("");
      setLoadMoreError("");

      try {
        const cursor = null;
        const data = await requestGalleryPage(cursor);
        if (galleryGenerationRef.current !== generation || listRequestIdRef.current !== requestId) return;

        applyPagination(data);
        setFirstPageItems(data);
        setPreviewErrors({});
        data.items.forEach((item) => {
          void loadPreviewForItem(item, generation);
        });
      } catch (loadError) {
        if (galleryGenerationRef.current !== generation || listRequestIdRef.current !== requestId) return;
        const message = galleryErrorMessage(loadError, "加载图库失败");
        setMetadataError(message);
        toast.error(message);
      } finally {
        if (galleryGenerationRef.current === generation && listRequestIdRef.current === requestId) {
          setIsInitialLoading(false);
          setIsRefreshing(false);
        }
      }
    },
    [applyPagination, loadPreviewForItem, setFirstPageItems, userId],
  );

  const handleLoadMore = useCallback(async () => {
    const cursor = nextCursor;
    if (!userId || !hasMore || !cursor || isLoadingMore || isRefreshing) return;

    const generation = galleryGenerationRef.current;
    const requestId = listRequestIdRef.current + 1;
    listRequestIdRef.current = requestId;
    setIsLoadingMore(true);
    setLoadMoreError("");

    try {
      const data = await requestGalleryPage(cursor);
      if (galleryGenerationRef.current !== generation || listRequestIdRef.current !== requestId) return;

      applyPagination(data);
      const newItems = appendItems(data.items);
      newItems.forEach((item) => {
        void loadPreviewForItem(item, generation);
      });
    } catch (loadError) {
      if (galleryGenerationRef.current !== generation || listRequestIdRef.current !== requestId) return;
      const message = galleryErrorMessage(loadError, "加载更多失败");
      setLoadMoreError(message);
      toast.error(message);
    } finally {
      if (galleryGenerationRef.current === generation && listRequestIdRef.current === requestId) {
        setIsLoadingMore(false);
      }
    }
  }, [appendItems, applyPagination, hasMore, isLoadingMore, isRefreshing, loadPreviewForItem, nextCursor, userId]);

  const handleRefresh = useCallback(() => {
    if (isInitialLoading || isRefreshing) return;
    void loadFirstPage("refresh");
  }, [isInitialLoading, isRefreshing, loadFirstPage]);

  const removeGalleryItem = useCallback(
    (id: string) => {
      const nextItems = itemsRef.current.filter((item) => item.id !== id);
      syncItemRefs(nextItems);
      setItems(nextItems);
      setPreviews((current) => {
        const removed = current[id];
        if (!removed) return current;
        const next = { ...current };
        delete next[id];
        URL.revokeObjectURL(removed.url);
        previewUrlsRef.current = next;
        return next;
      });
      setPreviewErrors((current) => {
        if (!(id in current)) return current;
        const next = { ...current };
        delete next[id];
        return next;
      });
    },
    [syncItemRefs],
  );

  useEffect(() => {
    if (isCheckingAuth) return;
    clearGalleryState();
    if (!session || !userId) return;
    void loadFirstPage("initial");
  }, [clearGalleryState, isCheckingAuth, loadFirstPage, session, sessionKey, userId]);

  useEffect(
    () => () => {
      galleryGenerationRef.current += 1;
      syncItemRefs([]);
      revokeAllPreviewUrls();
    },
    [revokeAllPreviewUrls, syncItemRefs],
  );

  useEffect(() => {
    if (!lightboxOpen) return;
    if (lightboxImages.length === 0) {
      setLightboxOpen(false);
      setLightboxIndex(0);
      return;
    }
    if (lightboxIndex >= lightboxImages.length) {
      setLightboxIndex(lightboxImages.length - 1);
    }
  }, [lightboxImages.length, lightboxIndex, lightboxOpen]);

  const getBlob = useCallback(async (item: GalleryImage) => {
    const existing = previewUrlsRef.current[item.id]?.blob;
    if (existing) return existing;
    return fetchGalleryImageContentBlob(item.id);
  }, []);

  const handleDownload = useCallback(
    async (item: GalleryImage) => {
      setBusy(item.id, true);
      try {
        const blob = await getBlob(item);
        downloadBlob(blob, `genapi-gallery-${item.id}.png`);
      } catch (downloadError) {
        toast.error(galleryErrorMessage(downloadError, "下载图片失败"));
      } finally {
        setBusy(item.id, false);
      }
    },
    [getBlob, setBusy],
  );

  const handleDelete = useCallback(
    async (item: GalleryImage) => {
      setBusy(item.id, true);
      try {
        await deleteGalleryImage(item.id);
        deletedItemIdsRef.current.add(item.id);
        removeGalleryItem(item.id);
        toast.success("已删除图片");
      } catch (deleteError) {
        toast.error(galleryErrorMessage(deleteError, "删除图片失败"));
      } finally {
        setBusy(item.id, false);
      }
    },
    [removeGalleryItem, setBusy],
  );

  const handleContinueEdit = useCallback(
    async (item: GalleryImage) => {
      if (!userId) return;
      setBusy(item.id, true);
      try {
        const blob = await getBlob(item);
        const dataUrl = await blobToDataUrl(blob);
        await saveImageEditHandoff(
          userId,
          {
            name: `gallery-${item.id}.png`,
            type: blob.type || "image/png",
            dataUrl,
          },
          {
            galleryId: item.id,
            prompt: item.prompt,
            model: item.model,
            size: galleryTargetSize(item),
          },
        );
        router.push("/image");
      } catch (handoffError) {
        toast.error(galleryErrorMessage(handoffError, "继续编辑失败"));
      } finally {
        setBusy(item.id, false);
      }
    },
    [getBlob, router, setBusy, userId],
  );

  if (isCheckingAuth || !session) {
    return (
      <div className="flex min-h-[40vh] items-center justify-center">
        <LoaderCircle className="size-5 animate-spin text-slate-400" />
      </div>
    );
  }

  const showInitialLoading = isInitialLoading && items.length === 0;
  const canLoadMore = hasMore && Boolean(nextCursor);

  return (
    <section className="space-y-6">
      <PageHeader
        eyebrow="Gallery"
        title="图库"
        description="查看近期生成的图片，下载成品，或把图片带回生图页继续编辑。"
        actions={
          <Button
            variant="outline"
            className="h-10 rounded-xl border-stone-200 bg-white/85"
            disabled={isInitialLoading || isRefreshing}
            onClick={handleRefresh}
          >
            <RefreshCw className={cn("size-4", isRefreshing && "animate-spin")} />
            刷新
          </Button>
        }
      />

      {metadataError ? (
        <div className="rounded-2xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm leading-6 text-rose-700">
          {metadataError}
        </div>
      ) : null}

      {showInitialLoading ? (
        <div className="grid min-h-[42vh] place-items-center rounded-[28px] border border-slate-200 bg-white/80">
          <div className="flex items-center gap-3 text-sm text-slate-500">
            <LoaderCircle className="size-5 animate-spin" />
            正在加载图库
          </div>
        </div>
      ) : metadataError && items.length === 0 ? (
        <EmptyState
          title="图库加载失败"
          description={metadataError}
          icon={<ImageIcon className="size-7" />}
          action={
            <Button className="rounded-xl bg-slate-950 text-white hover:bg-slate-800" onClick={handleRefresh}>
              <RefreshCw className="size-4" />
              重试
            </Button>
          }
        />
      ) : items.length === 0 && canLoadMore ? (
        <EmptyState
          title="正在整理图库"
          description="部分图片文件暂时不可用，可以继续加载后面的图片。"
          icon={<ImageIcon className="size-7" />}
          action={
            <Button className="rounded-xl bg-slate-950 text-white hover:bg-slate-800" disabled={isLoadingMore || isRefreshing} onClick={() => void handleLoadMore()}>
              {isLoadingMore ? <LoaderCircle className="size-4 animate-spin" /> : <RefreshCw className="size-4" />}
              继续加载
            </Button>
          }
        />
      ) : items.length === 0 ? (
        <EmptyState
          title="图库暂无图片"
          description="生成或编辑成功后的图片会自动保存在这里，默认保留 7 天。"
          icon={<ImageIcon className="size-7" />}
          action={
            <Button className="rounded-xl bg-slate-950 text-white hover:bg-slate-800" onClick={() => router.push("/image")}>
              <Sparkles className="size-4" />
              去生成图片
            </Button>
          }
        />
      ) : (
        <>
          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4">
            {items.map((item, index) => {
              const preview = previews[item.id];
              const previewError = previewErrors[item.id];
              const busy = Boolean(busyIds[item.id]);
              const targetSize = galleryTargetSize(item);
              const actualDimensions = galleryImageDimensions(item);
              return (
                <article key={item.id} className="overflow-hidden rounded-[8px] border border-slate-200 bg-white shadow-sm">
                  <button
                    type="button"
                    className="group relative block aspect-square w-full overflow-hidden bg-slate-100 text-left"
                    onClick={() => {
                      const lightboxItemIndex = lightboxImages.findIndex((image) => image.id === item.id);
                      if (lightboxItemIndex < 0) return;
                      setLightboxIndex(lightboxItemIndex);
                      setLightboxOpen(true);
                    }}
                    disabled={!preview}
                  >
                    {preview ? (
                      <img
                        src={preview.url}
                        alt={item.prompt || `图库图片 ${index + 1}`}
                        className="absolute inset-0 h-full w-full object-cover transition duration-200 group-hover:scale-[1.02]"
                      />
                    ) : previewError ? (
                      <div className="flex h-full flex-col items-center justify-center gap-2 px-4 text-center text-sm text-slate-500">
                        <ImageIcon className="size-6 text-slate-400" />
                        <span>{previewError}</span>
                      </div>
                    ) : (
                      <div className="flex h-full items-center justify-center text-slate-400">
                        <LoaderCircle className="size-5 animate-spin" />
                      </div>
                    )}
                  </button>
                  <div className="space-y-3 p-3">
                    <div className="min-h-12">
                      <div className="line-clamp-2 text-sm font-semibold leading-6 text-slate-950">
                        {item.prompt || item.revised_prompt || "未命名图片"}
                      </div>
                      <div className="mt-1 flex flex-wrap gap-2 text-xs text-slate-500">
                        <span>{targetSize ? `目标尺寸 ${targetSize}` : "默认尺寸"}</span>
                        {actualDimensions ? <span>实际尺寸 {actualDimensions}</span> : null}
                        <span>{formatBytes(galleryImageSizeBytes(item))}</span>
                        <span>到期 {formatDate(item.expires_at)}</span>
                      </div>
                    </div>
                    <div className="grid grid-cols-3 gap-2">
                      <Button variant="outline" size="sm" className="h-9 rounded-xl" disabled={busy} onClick={() => void handleDownload(item)}>
                        {busy ? <LoaderCircle className="size-4 animate-spin" /> : <Download className="size-4" />}
                        下载
                      </Button>
                      <Button variant="outline" size="sm" className="h-9 rounded-xl" disabled={busy} onClick={() => void handleContinueEdit(item)}>
                        <Sparkles className="size-4" />
                        编辑
                      </Button>
                      <Button
                        variant="outline"
                        size="sm"
                        className="h-9 rounded-xl border-rose-200 text-rose-700 hover:bg-rose-50"
                        disabled={busy}
                        onClick={() => void handleDelete(item)}
                      >
                        <Trash2 className="size-4" />
                        删除
                      </Button>
                    </div>
                  </div>
                </article>
              );
            })}
          </div>

          {loadMoreError ? (
            <div className="rounded-2xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm leading-6 text-rose-700">
              {loadMoreError}
            </div>
          ) : null}

          {hasMore ? (
            <div className="flex justify-center">
              <Button
                variant="outline"
                className="h-10 rounded-xl border-slate-200 bg-white px-5"
                disabled={!canLoadMore || isLoadingMore || isRefreshing}
                onClick={() => void handleLoadMore()}
              >
                {isLoadingMore ? <LoaderCircle className="size-4 animate-spin" /> : null}
                加载更多
              </Button>
            </div>
          ) : null}
        </>
      )}

      <ImageLightbox
        images={lightboxImages}
        currentIndex={lightboxIndex}
        open={lightboxOpen}
        onOpenChange={setLightboxOpen}
        onIndexChange={setLightboxIndex}
      />
    </section>
  );
}
