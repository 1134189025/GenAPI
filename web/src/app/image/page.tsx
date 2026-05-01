"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { History, LoaderCircle, Plus } from "lucide-react";
import { toast } from "sonner";

import { ImageComposer } from "@/app/image/components/image-composer";
import { ImageResults, type ImageLightboxItem } from "@/app/image/components/image-results";
import { ImageSidebar } from "@/app/image/components/image-sidebar";
import { ImageLightbox } from "@/components/image-lightbox";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { editImage, fetchAccounts, fetchMe, generateImage, type Account } from "@/lib/api";
import { useAuthGuard } from "@/lib/use-auth-guard";
import { AUTH_SESSION_BROADCAST_CHANNEL, getStoredAuthSession } from "@/store/auth";
import { consumeImageEditHandoff } from "@/store/image-edit-handoff";
import { getScopedImagePreferenceStorageKey } from "@/store/image-conversation-scope";
import {
  clearImageConversations,
  deleteImageConversation,
  getImageConversationStats,
  listImageConversations,
  saveImageConversation,
  saveImageConversations,
  type ImageConversation,
  type ImageConversationMode,
  type ImageTurn,
  type ImageTurnStatus,
  type StoredImage,
  type StoredReferenceImage,
} from "@/store/image-conversations";

const ACTIVE_CONVERSATION_STORAGE_KEY = "genapi:image_active_conversation_id";
const IMAGE_SIZE_STORAGE_KEY = "genapi:image_last_size";
const IMAGE_SIZE_PRESETS = new Set(["1024x1024", "1536x864", "864x1536", "1280x960", "960x1280"]);
const activeConversationQueueIds = new Set<string>();
let imageQueueDrainInProgress = false;

function buildConversationTitle(prompt: string) {
  const trimmed = prompt.trim();
  if (trimmed.length <= 12) {
    return trimmed;
  }
  return `${trimmed.slice(0, 12)}...`;
}

function formatConversationTime(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "";
  }
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

function formatAvailableQuota(accounts: Account[]) {
  const availableAccounts = accounts.filter((account) => account.status !== "禁用");
  return String(availableAccounts.reduce((sum, account) => sum + Math.max(0, account.quota), 0));
}

function createId() {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  return `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function normalizeImageSizePreference(value: string | null | undefined) {
  const normalized = String(value || "")
    .trim()
    .toLowerCase()
    .replace("×", "x")
    .replace(/\s+/g, "");
  return IMAGE_SIZE_PRESETS.has(normalized) ? normalized : "";
}

function readFileAsDataUrl(file: File) {
  return new Promise<string>((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ""));
    reader.onerror = () => reject(new Error("读取参考图失败"));
    reader.readAsDataURL(file);
  });
}

function dataUrlToFile(dataUrl: string, fileName: string, mimeType?: string) {
  const [header, content] = dataUrl.split(",", 2);
  const matchedMimeType = header.match(/data:(.*?);base64/)?.[1];
  const binary = atob(content || "");
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) {
    bytes[index] = binary.charCodeAt(index);
  }
  return new File([bytes], fileName, { type: mimeType || matchedMimeType || "image/png" });
}

function buildReferenceImageFromResult(image: StoredImage, fileName: string): StoredReferenceImage | null {
  if (!image.b64_json) {
    return null;
  }

  return {
    name: fileName,
    type: "image/png",
    dataUrl: `data:image/png;base64,${image.b64_json}`,
  };
}

function pickFallbackConversationId(conversations: ImageConversation[]) {
  const activeConversation = conversations.find((conversation) =>
    conversation.turns.some((turn) => turn.status === "queued" || turn.status === "generating"),
  );
  return activeConversation?.id ?? conversations[0]?.id ?? null;
}

function sortImageConversations(conversations: ImageConversation[]) {
  return [...conversations].sort((a, b) => b.updatedAt.localeCompare(a.updatedAt));
}

function countActiveImageRequests(conversations: ImageConversation[]) {
  return conversations.reduce(
    (sum, conversation) =>
      sum +
      conversation.turns.reduce(
        (turnSum, turn) =>
          turn.status === "queued" || turn.status === "generating"
            ? turnSum + turn.images.filter((image) => image.status !== "success" && image.status !== "error").length
            : turnSum,
        0,
      ),
    0,
  );
}

async function recoverConversationHistory(ownerId: string, items: ImageConversation[]) {
  const normalized = items.map((conversation) => {
    let changed = false;

    const turns = conversation.turns.map((turn) => {
      if (turn.status !== "queued" && turn.status !== "generating") {
        return turn;
      }

      const loadingCount = turn.images.filter((image) => image.status === "loading").length;
      if (loadingCount > 0) {
        const message = "页面刷新或任务中断，未完成的图片已标记为失败";
        changed = true;
        return {
          ...turn,
          status: "error" as const,
          error: message,
          images: turn.images.map((image) =>
            image.status === "loading" ? { ...image, status: "error" as const, error: message } : image,
          ),
        };
      }

      const failedCount = turn.images.filter((image) => image.status === "error").length;
      const successCount = turn.images.filter((image) => image.status === "success").length;
      const nextStatus: ImageTurnStatus =
        failedCount > 0 ? "error" : successCount > 0 ? "success" : "queued";
      const nextError = failedCount > 0 ? turn.error || `其中 ${failedCount} 张未成功生成` : undefined;
      if (nextStatus === turn.status && nextError === turn.error) {
        return turn;
      }

      changed = true;
      return {
        ...turn,
        status: nextStatus,
        error: nextError,
      };
    });

    if (!changed) {
      return conversation;
    }

    const lastTurn = turns.length > 0 ? turns[turns.length - 1] : null;
    return {
      ...conversation,
      turns,
      updatedAt: lastTurn?.createdAt || conversation.updatedAt,
    };
  });

  const changedConversations = normalized.filter((conversation, index) => conversation !== items[index]);
  if (changedConversations.length > 0) {
    await saveImageConversations(ownerId, normalized);
  }

  return normalized;
}

function ImagePageContent({ isAdmin, userId, sessionKey }: { isAdmin: boolean; userId: string; sessionKey: string }) {
  const didLoadQuotaRef = useRef(false);
  const didConsumeGalleryHandoffRef = useRef(false);
  const conversationsRef = useRef<ImageConversation[]>([]);
  const isImageQueueOwnerActiveRef = useRef(true);
  const resultsViewportRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const [imagePrompt, setImagePrompt] = useState("");
  const [imageCount, setImageCount] = useState("1");
  const [imageMode, setImageMode] = useState<ImageConversationMode>("generate");
  const [imageSize, setImageSize] = useState("");
  const [isHistoryOpen, setIsHistoryOpen] = useState(false);
  const [referenceImageFiles, setReferenceImageFiles] = useState<File[]>([]);
  const [referenceImages, setReferenceImages] = useState<StoredReferenceImage[]>([]);
  const [conversations, setConversations] = useState<ImageConversation[]>([]);
  const [selectedConversationId, setSelectedConversationId] = useState<string | null>(null);
  const [isLoadingHistory, setIsLoadingHistory] = useState(true);
  const [availableQuota, setAvailableQuota] = useState("加载中...");
  const [lightboxImages, setLightboxImages] = useState<ImageLightboxItem[]>([]);
  const [lightboxOpen, setLightboxOpen] = useState(false);
  const [lightboxIndex, setLightboxIndex] = useState(0);
  const [deleteConfirm, setDeleteConfirm] = useState<{ type: "one"; id: string } | { type: "all" } | null>(null);

  const parsedCount = useMemo(() => Math.max(1, Math.min(10, Number(imageCount) || 1)), [imageCount]);
  const activeConversationStorageKey = useMemo(
    () => getScopedImagePreferenceStorageKey(ACTIVE_CONVERSATION_STORAGE_KEY, userId),
    [userId],
  );
  const imageSizeStorageKey = useMemo(
    () => getScopedImagePreferenceStorageKey(IMAGE_SIZE_STORAGE_KEY, userId),
    [userId],
  );
  const selectedConversation = useMemo(
    () => conversations.find((item) => item.id === selectedConversationId) ?? null,
    [conversations, selectedConversationId],
  );
  const activeTaskCount = useMemo(() => countActiveImageRequests(conversations), [conversations]);
  const deleteConfirmTitle = deleteConfirm?.type === "all" ? "清空历史记录" : deleteConfirm?.type === "one" ? "删除对话" : "";
  const deleteConfirmDescription =
    deleteConfirm?.type === "all"
      ? "确认删除全部图片历史记录吗？删除后无法恢复。"
      : deleteConfirm?.type === "one"
        ? "确认删除这条图片对话吗？删除后无法恢复。"
        : "";

  useEffect(() => {
    conversationsRef.current = conversations;
  }, [conversations]);

  const isCurrentImageQueueOwner = useCallback(async () => {
    if (!isImageQueueOwnerActiveRef.current) {
      return false;
    }
    const storedSession = await getStoredAuthSession();
    if (storedSession?.subjectId !== userId || storedSession.key !== sessionKey) {
      isImageQueueOwnerActiveRef.current = false;
      return false;
    }
    return true;
  }, [sessionKey, userId]);

  useEffect(() => {
    isImageQueueOwnerActiveRef.current = true;

    const channel =
      typeof window !== "undefined" && typeof BroadcastChannel !== "undefined"
        ? new BroadcastChannel(AUTH_SESSION_BROADCAST_CHANNEL)
        : null;
    if (channel) {
      channel.onmessage = () => {
        void isCurrentImageQueueOwner();
      };
    }

    const handleFocus = () => {
      void isCurrentImageQueueOwner();
    };
    const handleVisibilityChange = () => {
      if (document.visibilityState === "visible") {
        void isCurrentImageQueueOwner();
      }
    };
    window.addEventListener("focus", handleFocus);
    document.addEventListener("visibilitychange", handleVisibilityChange);

    return () => {
      isImageQueueOwnerActiveRef.current = false;
      channel?.close();
      window.removeEventListener("focus", handleFocus);
      document.removeEventListener("visibilitychange", handleVisibilityChange);
    };
  }, [isCurrentImageQueueOwner]);

  useEffect(() => {
    let cancelled = false;

    const loadHistory = async () => {
      setIsLoadingHistory(true);
      try {
        const storedSize = typeof window !== "undefined" ? window.localStorage.getItem(imageSizeStorageKey) : null;
        setImageSize(normalizeImageSizePreference(storedSize));

        const items = await listImageConversations(userId);
        const normalizedItems = await recoverConversationHistory(userId, items);
        if (cancelled) {
          return;
        }

        conversationsRef.current = normalizedItems;
        setConversations(normalizedItems);
        const storedConversationId =
          typeof window !== "undefined" ? window.localStorage.getItem(activeConversationStorageKey) : null;
        const nextSelectedConversationId =
          (storedConversationId && normalizedItems.some((conversation) => conversation.id === storedConversationId)
            ? storedConversationId
            : null) ?? pickFallbackConversationId(normalizedItems);
        setSelectedConversationId(nextSelectedConversationId);
      } catch (error) {
        const message = error instanceof Error ? error.message : "读取会话记录失败";
        toast.error(message);
      } finally {
        if (!cancelled) {
          setIsLoadingHistory(false);
        }
      }
    };

    void loadHistory();
    return () => {
      cancelled = true;
    };
  }, [activeConversationStorageKey, imageSizeStorageKey, userId]);

  useEffect(() => {
    if (isLoadingHistory || didConsumeGalleryHandoffRef.current) {
      return;
    }
    didConsumeGalleryHandoffRef.current = true;
    let cancelled = false;

    const consumeHandoff = async () => {
      try {
        const handoff = await consumeImageEditHandoff(userId);
        if (!handoff || cancelled) {
          return;
        }
        setSelectedConversationId(null);
        setImageMode("edit");
        setImagePrompt("");
        setReferenceImages([handoff.image]);
        setReferenceImageFiles([dataUrlToFile(handoff.image.dataUrl, handoff.image.name, handoff.image.type)]);
        setImageSize(normalizeImageSizePreference(handoff.source.size));
        if (fileInputRef.current) {
          fileInputRef.current.value = "";
        }
        textareaRef.current?.focus();
        toast.success("已从图库加入参考图，继续输入描述即可编辑");
      } catch (error) {
        const message = error instanceof Error ? error.message : "读取图库参考图失败";
        toast.error(message);
      }
    };

    void consumeHandoff();
    return () => {
      cancelled = true;
    };
  }, [isLoadingHistory, userId]);

  const loadQuota = useCallback(async () => {
    if (!isAdmin) {
      try {
        const data = await fetchMe();
        const memberQuota = data.user.member_image_quota ?? 0;
        const totalQuota = data.user.total_image_quota ?? data.user.image_quota ?? 0;
        setAvailableQuota(memberQuota > 0 ? `${totalQuota}（会员 ${memberQuota}）` : String(totalQuota));
      } catch {
        setAvailableQuota((prev) => (prev === "加载中..." ? "--" : prev));
      }
      return;
    }
    try {
      const data = await fetchAccounts();
      setAvailableQuota(formatAvailableQuota(data.items));
    } catch {
      setAvailableQuota((prev) => (prev === "加载中..." ? "--" : prev));
    }
  }, [isAdmin]);

  useEffect(() => {
    if (didLoadQuotaRef.current) {
      return;
    }
    didLoadQuotaRef.current = true;

    const handleFocus = () => {
      void loadQuota();
    };

    void loadQuota();
    window.addEventListener("focus", handleFocus);
    return () => {
      window.removeEventListener("focus", handleFocus);
    };
  }, [isAdmin, loadQuota]);

  useEffect(() => {
    if (!selectedConversation) {
      return;
    }

    resultsViewportRef.current?.scrollTo({
      top: resultsViewportRef.current.scrollHeight,
      behavior: "smooth",
    });
  }, [selectedConversation?.updatedAt, selectedConversation?.turns.length, selectedConversation]);

  useEffect(() => {
    if (typeof window === "undefined") {
      return;
    }

    if (selectedConversationId) {
      window.localStorage.setItem(activeConversationStorageKey, selectedConversationId);
    } else {
      window.localStorage.removeItem(activeConversationStorageKey);
    }
  }, [activeConversationStorageKey, selectedConversationId]);

  useEffect(() => {
    if (typeof window === "undefined") {
      return;
    }

    if (imageSize) {
      const normalizedSize = normalizeImageSizePreference(imageSize);
      if (normalizedSize) {
        window.localStorage.setItem(imageSizeStorageKey, normalizedSize);
        return;
      }
      setImageSize("");
      window.localStorage.removeItem(imageSizeStorageKey);
      return;
    }
    window.localStorage.removeItem(imageSizeStorageKey);
  }, [imageSize, imageSizeStorageKey]);

  useEffect(() => {
    if (selectedConversationId && !conversations.some((conversation) => conversation.id === selectedConversationId)) {
      setSelectedConversationId(pickFallbackConversationId(conversations));
    }
  }, [conversations, selectedConversationId]);

  const persistConversation = async (conversation: ImageConversation) => {
    const nextConversations = sortImageConversations([
      conversation,
      ...conversationsRef.current.filter((item) => item.id !== conversation.id),
    ]);
    conversationsRef.current = nextConversations;
    setConversations(nextConversations);
    await saveImageConversation(userId, conversation);
  };

  const updateConversation = useCallback(
    async (
      conversationId: string,
      updater: (current: ImageConversation | null) => ImageConversation | null,
      options: { persist?: boolean } = {},
    ) => {
      const current = conversationsRef.current.find((item) => item.id === conversationId) ?? null;
      const nextConversation = updater(current);
      if (!nextConversation) {
        return null;
      }

      const nextConversations = sortImageConversations([
        nextConversation,
        ...conversationsRef.current.filter((item) => item.id !== conversationId),
      ]);
      conversationsRef.current = nextConversations;
      setConversations(nextConversations);
      if (options.persist !== false) {
        await saveImageConversation(userId, nextConversation);
      }
      return nextConversation;
    },
    [userId],
  );

  const clearComposerInputs = useCallback(() => {
    setImagePrompt("");
    setImageCount("1");
    setReferenceImageFiles([]);
    setReferenceImages([]);
    if (fileInputRef.current) {
      fileInputRef.current.value = "";
    }
  }, []);

  const resetComposer = useCallback(() => {
    setImageMode("generate");
    clearComposerInputs();
  }, [clearComposerInputs]);

  const handleCreateDraft = () => {
    setSelectedConversationId(null);
    resetComposer();
    textareaRef.current?.focus();
  };

  const handleDeleteConversation = async (id: string) => {
    const nextConversations = conversations.filter((item) => item.id !== id);
    conversationsRef.current = nextConversations;
    setConversations(nextConversations);
    if (selectedConversationId === id) {
      setSelectedConversationId(pickFallbackConversationId(nextConversations));
      resetComposer();
    }

    try {
      await deleteImageConversation(userId, id);
    } catch (error) {
      const message = error instanceof Error ? error.message : "删除会话失败";
      toast.error(message);
      const items = await listImageConversations(userId);
      conversationsRef.current = items;
      setConversations(items);
    }
  };

  const handleClearHistory = async () => {
    try {
      await clearImageConversations(userId);
      conversationsRef.current = [];
      setConversations([]);
      setSelectedConversationId(null);
      resetComposer();
      toast.success("已清空历史记录");
    } catch (error) {
      const message = error instanceof Error ? error.message : "清空历史记录失败";
      toast.error(message);
    }
  };

  const openDeleteConversationConfirm = (id: string) => {
    setIsHistoryOpen(false);
    setDeleteConfirm({ type: "one", id });
  };

  const openClearHistoryConfirm = () => {
    setIsHistoryOpen(false);
    setDeleteConfirm({ type: "all" });
  };

  const handleConfirmDelete = async () => {
    const target = deleteConfirm;
    setDeleteConfirm(null);
    if (!target) {
      return;
    }
    if (target.type === "all") {
      await handleClearHistory();
      return;
    }
    await handleDeleteConversation(target.id);
  };

  const appendReferenceImages = useCallback(async (files: File[]) => {
    if (files.length === 0) {
      return;
    }

    try {
      const previews = await Promise.all(
        files.map(async (file) => ({
          name: file.name,
          type: file.type || "image/png",
          dataUrl: await readFileAsDataUrl(file),
        })),
      );

      setReferenceImageFiles((prev) => [...prev, ...files]);
      setReferenceImages((prev) => [...prev, ...previews]);
      setImageMode("edit");
      if (fileInputRef.current) {
        fileInputRef.current.value = "";
      }
    } catch (error) {
      const message = error instanceof Error ? error.message : "读取参考图失败";
      toast.error(message);
    }
  }, []);

  const handleReferenceImageChange = useCallback(
    async (files: File[]) => {
      if (files.length === 0) {
        return;
      }

      await appendReferenceImages(files);
    },
    [appendReferenceImages],
  );

  const handleRemoveReferenceImage = useCallback((index: number) => {
    setReferenceImageFiles((prev) => {
      const next = prev.filter((_, currentIndex) => currentIndex !== index);
      if (next.length === 0 && fileInputRef.current) {
        fileInputRef.current.value = "";
      }
      return next;
    });
    setReferenceImages((prev) => prev.filter((_, currentIndex) => currentIndex !== index));
  }, []);

  const handleContinueEdit = useCallback(
    (conversationId: string, image: StoredImage | StoredReferenceImage) => {
      const nextReferenceImage =
        "dataUrl" in image
          ? image
          : buildReferenceImageFromResult(image, `conversation-${conversationId}-${Date.now()}.png`);
      if (!nextReferenceImage) {
        return;
      }

      setSelectedConversationId(conversationId);
      setImageMode("edit");
      setReferenceImages((prev) => [...prev, nextReferenceImage]);
      setReferenceImageFiles((prev) => [
        ...prev,
        dataUrlToFile(nextReferenceImage.dataUrl, nextReferenceImage.name, nextReferenceImage.type),
      ]);
      setImagePrompt("");
      textareaRef.current?.focus();
      toast.success("已加入当前参考图，继续输入描述即可编辑");
    },
    [],
  );

  const handleUsePrompt = useCallback((prompt: string) => {
    setImagePrompt(prompt);
    textareaRef.current?.focus();
  }, []);

  const openLightbox = useCallback((images: ImageLightboxItem[], index: number) => {
    if (images.length === 0) {
      return;
    }

    setLightboxImages(images);
    setLightboxIndex(Math.max(0, Math.min(index, images.length - 1)));
    setLightboxOpen(true);
  }, []);

  const runConversationQueue = useCallback(
    async (conversationId: string) => {
      if (!(await isCurrentImageQueueOwner())) {
        return;
      }

      if (activeConversationQueueIds.has(conversationId)) {
        return;
      }

      const snapshot = conversationsRef.current.find((conversation) => conversation.id === conversationId);
      const queuedTurn = snapshot?.turns.find((turn) => turn.status === "queued");
      if (!snapshot || !queuedTurn) {
        return;
      }

      const getCurrentQueuedTurn = () =>
        conversationsRef.current
          .find((conversation) => conversation.id === conversationId)
          ?.turns.find((turn) => turn.id === queuedTurn.id) ?? null;

      activeConversationQueueIds.add(conversationId);
      try {
        const startedConversation = await updateConversation(conversationId, (current) => {
          if (!current) {
            return null;
          }

          return {
            ...current,
            updatedAt: new Date().toISOString(),
            turns: current.turns.map((turn) =>
              turn.id === queuedTurn.id
                ? {
                    ...turn,
                    status: "generating",
                    error: undefined,
                  }
                : turn,
            ),
          };
        });
        if (!startedConversation) {
          return;
        }
        if (!(await isCurrentImageQueueOwner())) {
          return;
        }

        const referenceFiles = queuedTurn.referenceImages.map((image, index) =>
          dataUrlToFile(image.dataUrl, image.name || `${queuedTurn.id}-${index + 1}.png`, image.type),
        );
        const pendingImages = queuedTurn.images.filter((image) => image.status === "loading");

        if (queuedTurn.mode === "edit" && referenceFiles.length === 0) {
          throw new Error("未找到可用于继续编辑的参考图");
        }

        if (pendingImages.length === 0) {
          const existingFailedCount = queuedTurn.images.filter((image) => image.status === "error").length;
          const existingSuccessCount = queuedTurn.images.filter((image) => image.status === "success").length;
          await updateConversation(conversationId, (current) => {
            if (!current) {
              return null;
            }

            return {
              ...current,
              updatedAt: new Date().toISOString(),
              turns: current.turns.map((turn) =>
                turn.id === queuedTurn.id
                  ? {
                      ...turn,
                      status: existingFailedCount > 0 ? "error" : existingSuccessCount > 0 ? "success" : "queued",
                      error: existingFailedCount > 0 ? `其中 ${existingFailedCount} 张未成功生成` : undefined,
                    }
                  : turn,
              ),
            };
          });
          return;
        }

        let resumedSuccessCount = 0;
        let resumedFailedCount = 0;

        for (const pendingImage of pendingImages) {
          if (!(await isCurrentImageQueueOwner()) || !getCurrentQueuedTurn()) {
            break;
          }

          try {
            const data =
              queuedTurn.mode === "edit"
                ? await editImage(referenceFiles, queuedTurn.prompt, queuedTurn.model, queuedTurn.size, sessionKey)
                : await generateImage(queuedTurn.prompt, queuedTurn.model, queuedTurn.size, sessionKey);
            if (!(await isCurrentImageQueueOwner()) || !getCurrentQueuedTurn()) {
              break;
            }
            const first = data.data?.[0];
            if (!first?.b64_json && !first?.content_url && !first?.url) {
              throw new Error("未返回图片数据");
            }

            const nextImage: StoredImage = {
              id: pendingImage.id,
              status: "success",
              b64_json: first.b64_json,
              serverId: first.gallery_id,
              url: first.content_url || first.url,
              expiresAt: first.expires_at,
              width: typeof first.width === "number" ? first.width : undefined,
              height: typeof first.height === "number" ? first.height : undefined,
              targetSize: typeof first.target_size === "string" ? first.target_size : undefined,
              targetWidth:
                typeof first.target_width === "number" ? first.target_width : first.target_width === null ? null : undefined,
              targetHeight:
                typeof first.target_height === "number"
                  ? first.target_height
                  : first.target_height === null
                    ? null
                    : undefined,
              targetAspectRatio: typeof first.target_aspect_ratio === "string" ? first.target_aspect_ratio : undefined,
            };

            await updateConversation(
              conversationId,
              (current) => {
                if (!current) {
                  return null;
                }

                return {
                  ...current,
                  updatedAt: new Date().toISOString(),
                  turns: current.turns.map((turn) =>
                    turn.id === queuedTurn.id
                      ? {
                          ...turn,
                          images: turn.images.map((image) => (image.id === nextImage.id ? nextImage : image)),
                        }
                      : turn,
                  ),
                };
              },
            );

            resumedSuccessCount += 1;
          } catch (error) {
            if (!(await isCurrentImageQueueOwner()) || !getCurrentQueuedTurn()) {
              break;
            }
            const message = error instanceof Error ? error.message : "生成失败";
            const failedImage: StoredImage = {
              id: pendingImage.id,
              status: "error",
              error: message,
            };

            await updateConversation(
              conversationId,
              (current) => {
                if (!current) {
                  return null;
                }

                return {
                  ...current,
                  updatedAt: new Date().toISOString(),
                  turns: current.turns.map((turn) =>
                    turn.id === queuedTurn.id
                      ? {
                          ...turn,
                          images: turn.images.map((image) => (image.id === failedImage.id ? failedImage : image)),
                        }
                      : turn,
                  ),
                };
              },
              { persist: false },
            );

            resumedFailedCount += 1;
          }
        }
        if (!(await isCurrentImageQueueOwner())) {
          return;
        }
        const existingSuccessCount = queuedTurn.images.filter((image) => image.status === "success").length;
        const existingFailedCount = queuedTurn.images.filter((image) => image.status === "error").length;
        const successCount = existingSuccessCount + resumedSuccessCount;
        const failedCount = existingFailedCount + resumedFailedCount;

        await updateConversation(conversationId, (current) => {
          if (!current) {
            return null;
          }

          return {
            ...current,
            updatedAt: new Date().toISOString(),
            turns: current.turns.map((turn) =>
              turn.id === queuedTurn.id
                ? {
                    ...turn,
                    status: failedCount > 0 ? "error" : "success",
                    error: failedCount > 0 ? `其中 ${failedCount} 张未成功生成` : undefined,
                  }
                : turn,
            ),
          };
        });

        if (await isCurrentImageQueueOwner()) {
          await loadQuota();
        }
      } catch (error) {
        if (!(await isCurrentImageQueueOwner())) {
          return;
        }
        const message = error instanceof Error ? error.message : "生成图片失败";
        await updateConversation(conversationId, (current) => {
          if (!current) {
            return null;
          }

          return {
            ...current,
            updatedAt: new Date().toISOString(),
            turns: current.turns.map((turn) =>
              turn.id === queuedTurn.id
                ? {
                    ...turn,
                    status: "error",
                    error: message,
                    images: turn.images.map((image) =>
                      image.status === "loading" ? { ...image, status: "error", error: message } : image,
                    ),
                  }
                : turn,
            ),
          };
        });
        toast.error(message);
      } finally {
        activeConversationQueueIds.delete(conversationId);
      }
    },
    [isCurrentImageQueueOwner, loadQuota, sessionKey, updateConversation],
  );

  const drainConversationQueues = useCallback(async () => {
    if (imageQueueDrainInProgress) {
      return;
    }

    imageQueueDrainInProgress = true;
    try {
      while (isImageQueueOwnerActiveRef.current) {
        const nextConversation = conversationsRef.current.find(
          (conversation) =>
            !activeConversationQueueIds.has(conversation.id) &&
            conversation.turns.some((turn) => turn.status === "queued"),
        );
        if (!nextConversation) {
          return;
        }

        await runConversationQueue(nextConversation.id);
      }
    } finally {
      imageQueueDrainInProgress = false;
    }
  }, [runConversationQueue]);

  useEffect(() => {
    void drainConversationQueues();
  }, [conversations, drainConversationQueues]);

  const handleSubmit = async () => {
    const prompt = imagePrompt.trim();
    if (!prompt) {
      toast.error("请输入提示词");
      return;
    }

    if (imageMode === "edit" && referenceImageFiles.length === 0) {
      toast.error("请先上传参考图");
      return;
    }

    const targetConversation = selectedConversationId
      ? conversationsRef.current.find((conversation) => conversation.id === selectedConversationId) ?? null
      : null;
    const now = new Date().toISOString();
    const conversationId = targetConversation?.id ?? createId();
    const turnId = createId();
    const selectedImageSize = normalizeImageSizePreference(imageSize);
    if (imageSize && !selectedImageSize) {
      setImageSize("");
    }
    const draftTurn: ImageTurn = {
      id: turnId,
      prompt,
      model: "gpt-image-2",
      mode: imageMode,
      referenceImages: imageMode === "edit" ? referenceImages : [],
      count: parsedCount,
      size: selectedImageSize,
      images: Array.from({ length: parsedCount }, (_, index) => ({
        id: `${turnId}-${index}`,
        status: "loading" as const,
      })),
      createdAt: now,
      status: "queued",
    };

    const baseConversation: ImageConversation = targetConversation
      ? {
          ...targetConversation,
          updatedAt: now,
          turns: [...targetConversation.turns, draftTurn],
        }
      : {
          id: conversationId,
          title: buildConversationTitle(prompt),
          createdAt: now,
          updatedAt: now,
          turns: [draftTurn],
        };

    setSelectedConversationId(conversationId);
    clearComposerInputs();

    await persistConversation(baseConversation);
    void drainConversationQueues();

    const targetStats = getImageConversationStats(baseConversation);
    if (targetStats.running > 0 || targetStats.queued > 1) {
      toast.success("已加入当前对话队列");
    } else if (!targetConversation) {
      toast.success("已创建新对话并开始处理");
    } else {
      toast.success("已发送到当前对话");
    }
  };

  return (
    <>
      <section className="relative mx-auto flex h-[calc(100vh-5rem)] min-h-0 w-full max-w-[1180px] flex-col px-3 pb-4 sm:px-5">
        <div className="pointer-events-none absolute inset-x-4 top-2 -z-10 h-40 rounded-full bg-[radial-gradient(circle_at_center,rgba(214,211,209,0.55),transparent_70%)] blur-3xl" />
        <div className="flex shrink-0 items-center justify-between gap-3 py-3">
          <div className="min-w-0">
            <h1 className="truncate text-lg font-semibold tracking-tight text-stone-950 sm:text-xl">生成图片</h1>
            <p className="hidden text-sm text-stone-500 sm:block">描述画面，生成结果，满意后继续编辑。</p>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            <div className="hidden rounded-full bg-white/85 px-3 py-2 text-xs font-medium text-stone-600 shadow-sm ring-1 ring-stone-200 sm:block">
              额度 {availableQuota}
            </div>
            {activeTaskCount > 0 ? (
              <div className="hidden items-center gap-1.5 rounded-full bg-amber-50 px-3 py-2 text-xs font-medium text-amber-700 ring-1 ring-amber-100 sm:flex">
                <LoaderCircle className="size-3.5 animate-spin" />
                {activeTaskCount} 个处理中
              </div>
            ) : null}
            <Button
              variant="outline"
              className="h-10 rounded-full border-stone-200 bg-white/85 px-3 text-stone-700 shadow-sm sm:px-4"
              onClick={() => setIsHistoryOpen(true)}
            >
              <History className="size-4 sm:mr-2" />
              <span className="hidden sm:inline">历史</span>
              <span className="ml-1 text-xs text-stone-400 sm:ml-2">{conversations.length}</span>
            </Button>
            <Button
              className="h-10 rounded-full bg-stone-950 px-3 text-white shadow-sm hover:bg-stone-800 sm:px-4"
              onClick={handleCreateDraft}
            >
              <Plus className="size-4 sm:mr-2" />
              <span className="hidden sm:inline">新建</span>
            </Button>
          </div>
        </div>

        <div
          ref={resultsViewportRef}
          className="hide-scrollbar min-h-0 flex-1 overflow-y-auto rounded-[34px] border border-stone-200/70 bg-stone-50/50 px-2 py-4 sm:px-5 sm:py-6"
        >
          <ImageResults
            selectedConversation={selectedConversation}
            onOpenLightbox={openLightbox}
            onContinueEdit={handleContinueEdit}
            formatConversationTime={formatConversationTime}
            onUsePrompt={handleUsePrompt}
          />
        </div>

        <div className="shrink-0 pt-3">
          <ImageComposer
            mode={imageMode}
            prompt={imagePrompt}
            imageCount={imageCount}
            imageSize={imageSize}
            availableQuota={availableQuota}
            activeTaskCount={activeTaskCount}
            referenceImages={referenceImages}
            textareaRef={textareaRef}
            fileInputRef={fileInputRef}
            onModeChange={setImageMode}
            onPromptChange={setImagePrompt}
            onImageCountChange={setImageCount}
            onImageSizeChange={setImageSize}
            onSubmit={handleSubmit}
            onPickReferenceImage={() => fileInputRef.current?.click()}
            onReferenceImageChange={handleReferenceImageChange}
            onRemoveReferenceImage={handleRemoveReferenceImage}
          />
        </div>
      </section>

      <Dialog open={isHistoryOpen} onOpenChange={setIsHistoryOpen}>
        <DialogContent className="flex h-[82vh] w-[92vw] max-w-[440px] flex-col overflow-hidden rounded-[32px] border-stone-200 bg-white p-0 shadow-2xl">
          <DialogHeader className="px-6 pt-6 pb-2">
            <DialogTitle className="flex items-center gap-2 text-lg font-bold">
              <History className="size-5" />
              历史记录
            </DialogTitle>
          </DialogHeader>
          <div className="min-h-0 flex-1 overflow-y-auto px-4 pb-6">
            <ImageSidebar
              conversations={conversations}
              isLoadingHistory={isLoadingHistory}
              selectedConversationId={selectedConversationId}
              onCreateDraft={() => {
                handleCreateDraft();
                setIsHistoryOpen(false);
              }}
              onClearHistory={openClearHistoryConfirm}
              onSelectConversation={(id) => {
                setSelectedConversationId(id);
                setIsHistoryOpen(false);
              }}
              onDeleteConversation={openDeleteConversationConfirm}
              formatConversationTime={formatConversationTime}
            />
          </div>
        </DialogContent>
      </Dialog>

      <ImageLightbox
        images={lightboxImages}
        currentIndex={lightboxIndex}
        open={lightboxOpen}
        onOpenChange={setLightboxOpen}
        onIndexChange={setLightboxIndex}
      />

      {deleteConfirm ? (
        <Dialog open onOpenChange={(open) => (!open ? setDeleteConfirm(null) : null)}>
          <DialogContent showCloseButton={false} className="rounded-2xl p-6">
            <DialogHeader className="gap-2">
              <DialogTitle>{deleteConfirmTitle}</DialogTitle>
              <DialogDescription className="text-sm leading-6">
                {deleteConfirmDescription}
              </DialogDescription>
            </DialogHeader>
            <DialogFooter>
              <Button variant="outline" onClick={() => setDeleteConfirm(null)}>
                取消
              </Button>
              <Button className="bg-rose-600 text-white hover:bg-rose-700" onClick={() => void handleConfirmDelete()}>
                确认删除
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      ) : null}
    </>
  );
}

export default function ImagePage() {
  const { isCheckingAuth, session } = useAuthGuard();

  if (isCheckingAuth || !session) {
    return (
      <div className="flex min-h-[40vh] items-center justify-center">
        <LoaderCircle className="size-5 animate-spin text-stone-400" />
      </div>
    );
  }

  const userId = String(session.subjectId || "").trim();
  if (!userId) {
    return (
      <div className="mx-auto grid min-h-[40vh] max-w-md place-items-center px-4 text-center">
        <div>
          <div className="text-base font-black text-slate-950">无法确认当前用户</div>
          <p className="mt-2 text-sm leading-6 text-slate-500">请重新登录后再打开图片页面。</p>
        </div>
      </div>
    );
  }

  return <ImagePageContent key={userId} isAdmin={session.role === "admin"} userId={userId} sessionKey={session.key} />;
}
