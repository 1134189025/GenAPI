"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { History, LoaderCircle, Plus } from "lucide-react";
import { toast } from "sonner";

import { ImageComposer } from "@/app/image/components/image-composer";
import { ImageResults, type ImageLightboxItem } from "@/app/image/components/image-results";
import { ImageSidebar } from "@/app/image/components/image-sidebar";
import {
  MAX_IMAGE_BATCH_CONCURRENCY,
  resolveImageBatchConcurrencyLimit,
  runBoundedImageBatch,
} from "@/app/image/image-batch-runner";
import { getImageQueueRuntime, hasLiveImageQueueWork, notifyImageQueueRuntime } from "@/app/image/image-queue-runtime";
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
import { formatImageCostGgb, formatQuotaAsGgb, normalizeQuotaErrorMessage } from "@/lib/ggb";
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
const IMAGE_SIZE_PRESETS = new Set([
  "1024x1024",
  "1536x864",
  "864x1536",
  "1280x960",
  "960x1280",
  "1920x1080",
  "1080x1920",
  "2560x1440",
  "1440x2560",
  "3840x2160",
  "2160x3840",
]);

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
  const availableAccounts = accounts.filter((account) => account.status === "正常");
  return `${availableAccounts.reduce((sum, account) => sum + Math.max(0, account.quota), 0)} 上游额度`;
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
  return new File([bytes], fileName, {
    type: mimeType || matchedMimeType || "image/png",
  });
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

function markAbortedImageQueueRunsFailed(
  conversations: ImageConversation[],
  message: string,
  conversationIds?: string[],
) {
  const targetIds = conversationIds ? new Set(conversationIds) : null;
  const updatedAt = new Date().toISOString();

  return conversations.map((conversation) => {
    if (targetIds && !targetIds.has(conversation.id)) {
      return conversation;
    }

    let changed = false;
    const turns = conversation.turns.map((turn) => {
      if (turn.status !== "queued" && turn.status !== "generating") {
        return turn;
      }

      changed = true;
      return {
        ...turn,
        status: "error" as const,
        error: message,
        images: turn.images.map((image) =>
          image.status === "loading" ? { ...image, status: "error" as const, error: message } : image,
        ),
      };
    });

    return changed ? { ...conversation, turns, updatedAt } : conversation;
  });
}

async function recoverConversationHistory(ownerId: string, items: ImageConversation[]) {
  const latestStoredUpdatedAt = Math.max(
    Date.now(),
    ...items.map((conversation) => {
      const timestamp = new Date(conversation.updatedAt).getTime();
      return Number.isFinite(timestamp) ? timestamp : 0;
    }),
  );
  const recoveryUpdatedAt = new Date(latestStoredUpdatedAt + 1).toISOString();
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
      const nextStatus: ImageTurnStatus = failedCount > 0 ? "error" : successCount > 0 ? "success" : "error";
      const nextError =
        failedCount > 0
          ? turn.error || `其中 ${failedCount} 张未成功生成`
          : successCount > 0
            ? undefined
            : turn.error || "图片生成未完成";

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

    return {
      ...conversation,
      turns,
      updatedAt: recoveryUpdatedAt,
    };
  });

  const changedConversations = normalized.filter((conversation, index) => conversation !== items[index]);
  if (changedConversations.length > 0) {
    await saveImageConversations(ownerId, normalized);
  }

  return normalized;
}

function ImagePageContent({
  isAdmin,
  userId,
  sessionKey,
  sessionName,
}: {
  isAdmin: boolean;
  userId: string;
  sessionKey: string;
  sessionName: string;
}) {
  const imageQueueRuntime = useMemo(() => getImageQueueRuntime(sessionKey), [sessionKey]);
  const didLoadQuotaRef = useRef(false);
  const didConsumeGalleryHandoffRef = useRef(false);
  const conversationsRef = useRef<ImageConversation[]>([]);
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
  const [accountDisplayName, setAccountDisplayName] = useState(sessionName || "当前账号");
  const [membershipLevelLabel, setMembershipLevelLabel] = useState(isAdmin ? "管理员" : "普通用户");
  const [imageConcurrencyLimit, setImageConcurrencyLimit] = useState(isAdmin ? MAX_IMAGE_BATCH_CONCURRENCY : 1);
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
  const deleteConfirmTitle =
    deleteConfirm?.type === "all" ? "清空历史记录" : deleteConfirm?.type === "one" ? "删除对话" : "";
  const deleteConfirmDescription =
    deleteConfirm?.type === "all"
      ? "确认删除全部图片历史记录吗？删除后无法恢复。"
      : deleteConfirm?.type === "one"
        ? "确认删除这条图片对话吗？删除后无法恢复。"
        : "";

  useEffect(() => {
    conversationsRef.current = conversations;
  }, [conversations]);

  useEffect(() => {
    imageQueueRuntime.userId = userId;
    imageQueueRuntime.sessionKey = sessionKey;
    imageQueueRuntime.isActive = true;
    const syncRuntimeConversations = (items: ImageConversation[]) => {
      conversationsRef.current = items;
      setConversations(items);
    };
    imageQueueRuntime.listeners.add(syncRuntimeConversations);
    if (imageQueueRuntime.conversations.length > 0) {
      syncRuntimeConversations(imageQueueRuntime.conversations);
    }
    return () => {
      imageQueueRuntime.listeners.delete(syncRuntimeConversations);
    };
  }, [imageQueueRuntime, sessionKey, userId]);

  const abortImageConversationRequest = useCallback((conversationId: string) => {
    const controller = imageQueueRuntime.abortControllers.get(conversationId);
    if (!controller) {
      return;
    }
    controller.abort();
    imageQueueRuntime.abortControllers.delete(conversationId);
  }, [imageQueueRuntime]);

  const abortAllImageConversationRequests = useCallback(() => {
    imageQueueRuntime.abortControllers.forEach((controller) => controller.abort());
    imageQueueRuntime.abortControllers.clear();
  }, [imageQueueRuntime]);

  const deactivateImageQueueOwner = useCallback((ownerSessionKey: string) => {
    if (imageQueueRuntime.sessionKey !== ownerSessionKey) {
      return;
    }

    abortAllImageConversationRequests();
    imageQueueRuntime.isActive = false;
    imageQueueRuntime.drainInProgress = false;
  }, [abortAllImageConversationRequests, imageQueueRuntime]);

  const isCurrentImageQueueOwner = useCallback(async () => {
    if (!imageQueueRuntime.isActive) {
      return false;
    }
    if (imageQueueRuntime.sessionKey !== sessionKey) {
      return false;
    }
    const storedSession = await getStoredAuthSession();
    if (storedSession?.subjectId !== userId || storedSession.key !== sessionKey) {
      deactivateImageQueueOwner(sessionKey);
      return false;
    }
    return true;
  }, [deactivateImageQueueOwner, imageQueueRuntime, sessionKey, userId]);

  useEffect(() => {
    imageQueueRuntime.sessionKey = sessionKey;
    imageQueueRuntime.userId = userId;
    imageQueueRuntime.isActive = true;

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
      channel?.close();
      window.removeEventListener("focus", handleFocus);
      document.removeEventListener("visibilitychange", handleVisibilityChange);
    };
  }, [imageQueueRuntime, isCurrentImageQueueOwner, sessionKey, userId]);

  useEffect(() => {
    let cancelled = false;

    const loadHistory = async () => {
      setIsLoadingHistory(true);
      try {
        const storedSize = typeof window !== "undefined" ? window.localStorage.getItem(imageSizeStorageKey) : null;
        setImageSize(normalizeImageSizePreference(storedSize));

        const items = imageQueueRuntime.conversations.length > 0
          ? imageQueueRuntime.conversations
          : await listImageConversations(userId);
        const normalizedItems = hasLiveImageQueueWork(sessionKey)
          ? sortImageConversations(items)
          : await recoverConversationHistory(userId, items);
        if (cancelled) {
          return;
        }

        conversationsRef.current = normalizedItems;
        imageQueueRuntime.conversations = normalizedItems;
        setConversations(normalizedItems);
        notifyImageQueueRuntime(imageQueueRuntime);
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
  }, [activeConversationStorageKey, imageQueueRuntime, imageSizeStorageKey, sessionKey, userId]);

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
        setAccountDisplayName(data.user.email || data.user.name || sessionName || "当前账号");
        setMembershipLevelLabel(
          data.user.membership_status === "active" ? data.user.membership_plan_name || "会员" : "普通用户",
        );
        setImageConcurrencyLimit(resolveImageBatchConcurrencyLimit(data.user.image_concurrency));
        setAvailableQuota(
          memberQuota > 0
            ? `${formatQuotaAsGgb(totalQuota)}（会员 ${formatQuotaAsGgb(memberQuota)}）`
            : formatQuotaAsGgb(totalQuota),
        );
      } catch {
        setAvailableQuota((prev) => (prev === "加载中..." ? "--" : prev));
      }
      return;
    }
    try {
      const data = await fetchAccounts();
      setAccountDisplayName(sessionName || "管理员");
      setMembershipLevelLabel("管理员");
      setImageConcurrencyLimit(MAX_IMAGE_BATCH_CONCURRENCY);
      setAvailableQuota(formatAvailableQuota(data.items));
    } catch {
      setAvailableQuota((prev) => (prev === "加载中..." ? "--" : prev));
    }
  }, [isAdmin, sessionName]);

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
      ...imageQueueRuntime.conversations.filter((item) => item.id !== conversation.id),
    ]);
    imageQueueRuntime.conversations = nextConversations;
    conversationsRef.current = nextConversations;
    setConversations(nextConversations);
    notifyImageQueueRuntime(imageQueueRuntime);
    await saveImageConversation(userId, conversation);
  };

  const updateConversation = useCallback(
    async (
      conversationId: string,
      updater: (current: ImageConversation | null) => ImageConversation | null,
      options: { persist?: boolean } = {},
    ) => {
      const current = imageQueueRuntime.conversations.find((item) => item.id === conversationId) ?? null;
      const nextConversation = updater(current);
      if (!nextConversation) {
        return null;
      }

      const nextConversations = sortImageConversations([
        nextConversation,
        ...imageQueueRuntime.conversations.filter((item) => item.id !== conversationId),
      ]);
      imageQueueRuntime.conversations = nextConversations;
      conversationsRef.current = nextConversations;
      setConversations(nextConversations);
      notifyImageQueueRuntime(imageQueueRuntime);
      if (options.persist !== false) {
        await saveImageConversation(userId, nextConversation);
      }
      return nextConversation;
    },
    [imageQueueRuntime, userId],
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
    abortImageConversationRequest(id);
    const previousConversations = imageQueueRuntime.conversations;
    const nextConversations = previousConversations.filter((item) => item.id !== id);
    imageQueueRuntime.conversations = nextConversations;
    conversationsRef.current = nextConversations;
    setConversations(nextConversations);
    notifyImageQueueRuntime(imageQueueRuntime);
    if (selectedConversationId === id) {
      setSelectedConversationId(pickFallbackConversationId(nextConversations));
      resetComposer();
    }

    try {
      await deleteImageConversation(userId, id);
    } catch (error) {
      const message = error instanceof Error ? error.message : "删除会话失败";
      toast.error(message);
      const abortedPreviousConversations = markAbortedImageQueueRunsFailed(
        previousConversations,
        "删除失败，已取消未完成的图片请求",
        [id],
      );
      const restoredConversation = abortedPreviousConversations.find((item) => item.id === id);
      const items = restoredConversation
        ? sortImageConversations([restoredConversation, ...imageQueueRuntime.conversations])
        : imageQueueRuntime.conversations;
      imageQueueRuntime.conversations = items;
      conversationsRef.current = items;
      setConversations(items);
      notifyImageQueueRuntime(imageQueueRuntime);
    }
  };

  const handleClearHistory = async () => {
    const previousConversations = imageQueueRuntime.conversations;
    const previousSelectedConversationId = selectedConversationId;
    try {
      abortAllImageConversationRequests();
      imageQueueRuntime.conversations = [];
      conversationsRef.current = [];
      setConversations([]);
      notifyImageQueueRuntime(imageQueueRuntime);
      setSelectedConversationId(null);
      resetComposer();
      await clearImageConversations(userId);
      toast.success("已清空历史记录");
    } catch (error) {
      const message = error instanceof Error ? error.message : "清空历史记录失败";
      toast.error(message);
      const restoredConversations = markAbortedImageQueueRunsFailed(
        previousConversations,
        "清空失败，已取消未完成的图片请求",
      );
      imageQueueRuntime.conversations = restoredConversations;
      conversationsRef.current = restoredConversations;
      setConversations(restoredConversations);
      notifyImageQueueRuntime(imageQueueRuntime);
      setSelectedConversationId(previousSelectedConversationId);
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

  const handleContinueEdit = useCallback((conversationId: string, image: StoredImage | StoredReferenceImage) => {
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
  }, []);

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

      const activeConversationQueueIds = imageQueueRuntime.activeConversationQueueIds;
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
      const markCurrentQueuedTurnCancelled = async () => {
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
                turn.id === queuedTurn.id && (turn.status === "queued" || turn.status === "generating")
                  ? {
                      ...turn,
                      status: "error",
                      error: "图片请求已取消",
                      images: turn.images.map((image) =>
                        image.status === "loading" ? { ...image, status: "error" as const, error: "图片请求已取消" } : image,
                      ),
                    }
                  : turn,
              ),
            };
          },
          { persist: false },
        );
      };

      const abortController = new AbortController();
      imageQueueRuntime.abortControllers.set(conversationId, abortController);
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
        const queuedTurnId = queuedTurn.id;
        const queuedTurnMode = queuedTurn.mode;
        const queuedTurnPrompt = queuedTurn.prompt;
        const queuedTurnModel = queuedTurn.model;
        const queuedTurnSize = queuedTurn.size;

        if (queuedTurn.mode === "edit" && referenceFiles.length === 0) {
          throw new Error("未找到可用于继续编辑的参考图");
        }

        if (pendingImages.length === 0) {
          const existingFailedCount = queuedTurn.images.filter((image) => image.status === "error").length;
          const existingSuccessCount = queuedTurn.images.filter((image) => image.status === "success").length;
          const nextStatus: ImageTurnStatus =
            existingFailedCount > 0 ? "error" : existingSuccessCount > 0 ? "success" : "error";
          const nextError =
            existingFailedCount > 0
              ? `其中 ${existingFailedCount} 张未成功生成`
              : existingSuccessCount > 0
                ? undefined
                : "图片生成未完成";
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
                      status: nextStatus,
                      error: nextError,
                    }
                  : turn,
              ),
            };
          });
          return;
        }

        async function runPendingImagesWithConcurrency() {
          const normalizedLimit = resolveImageBatchConcurrencyLimit(imageConcurrencyLimit);
          let resumedSuccessCount = 0;
          let resumedFailedCount = 0;
          let interrupted = false;
          let imagePersistChain = Promise.resolve();

          const canContinueImageBatch = async () => {
            if (abortController.signal.aborted) {
              interrupted = true;
              return false;
            }
            if (!(await isCurrentImageQueueOwner()) || !getCurrentQueuedTurn()) {
              interrupted = true;
              return false;
            }
            return true;
          };

          const persistUpdatedImageConversation = async (conversation: ImageConversation) => {
            imagePersistChain = imagePersistChain.then(async () => {
              const current = imageQueueRuntime.conversations.find((item) => item.id === conversation.id);
              if (current !== conversation) {
                return;
              }
              await saveImageConversation(userId, conversation);
            });
            await imagePersistChain;
          };

          const updatePendingImage = async (nextImage: StoredImage) => {
            let updated = false;
            const updatedConversation = await updateConversation(
              conversationId,
              (current) => {
                if (!current) {
                  return null;
                }

                const targetTurn = current.turns.find((turn) => turn.id === queuedTurnId);
                const targetImage = targetTurn?.images.find((image) => image.id === nextImage.id);
                if (
                  !targetTurn ||
                  (targetTurn.status !== "queued" && targetTurn.status !== "generating") ||
                  targetImage?.status !== "loading"
                ) {
                  return null;
                }

                updated = true;
                return {
                  ...current,
                  updatedAt: new Date().toISOString(),
                  turns: current.turns.map((turn) =>
                    turn.id === queuedTurnId
                      ? {
                          ...turn,
                          images: turn.images.map((image) => (image.id === nextImage.id ? nextImage : image)),
                        }
                      : turn,
                  ),
                };
              },
              { persist: false },
            );
            if (!updatedConversation) {
              interrupted = true;
              return false;
            }
            if (!updated) {
              interrupted = true;
              return false;
            }
            await persistUpdatedImageConversation(updatedConversation);
            return true;
          };

          await runBoundedImageBatch({
            items: pendingImages,
            concurrencyLimit: normalizedLimit,
            shouldContinue: canContinueImageBatch,
            runItem: async (pendingImage) => {
              try {
                const data =
                  queuedTurnMode === "edit"
                    ? await editImage(
                        referenceFiles,
                        queuedTurnPrompt,
                        queuedTurnModel,
                        queuedTurnSize,
                        sessionKey,
                        abortController.signal,
                      )
                    : await generateImage(
                        queuedTurnPrompt,
                        queuedTurnModel,
                        queuedTurnSize,
                        sessionKey,
                        abortController.signal,
                      );
                if (!(await canContinueImageBatch())) {
                  return;
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
                    typeof first.target_width === "number"
                      ? first.target_width
                      : first.target_width === null
                        ? null
                        : undefined,
                  targetHeight:
                    typeof first.target_height === "number"
                      ? first.target_height
                      : first.target_height === null
                        ? null
                        : undefined,
                  targetAspectRatio: typeof first.target_aspect_ratio === "string" ? first.target_aspect_ratio : undefined,
                };

                if (await updatePendingImage(nextImage)) {
                  resumedSuccessCount += 1;
                }
              } catch (error) {
                if (!(await canContinueImageBatch())) {
                  return;
                }
                const message = normalizeQuotaErrorMessage(error, "生成失败");
                const failedImage: StoredImage = {
                  id: pendingImage.id,
                  status: "error",
                  error: message,
                };

                if (await updatePendingImage(failedImage)) {
                  resumedFailedCount += 1;
                }
              }
            },
          });

          return { resumedSuccessCount, resumedFailedCount, interrupted };
        }

        const batchResult = await runPendingImagesWithConcurrency();
        if (batchResult.interrupted && !abortController.signal.aborted) {
          return;
        }
        if (abortController.signal.aborted) {
          await markCurrentQueuedTurnCancelled();
          return;
        }
        if (!(await isCurrentImageQueueOwner())) {
          return;
        }

        await updateConversation(conversationId, (current) => {
          if (!current) {
            return null;
          }

          const latestTurn = current.turns.find((turn) => turn.id === queuedTurn.id);
          const successCount = latestTurn?.images.filter((image) => image.status === "success").length ?? 0;
          const failedCount = latestTurn?.images.filter((image) => image.status === "error").length ?? 0;
          const loadingCount = latestTurn?.images.filter((image) => image.status === "loading").length ?? 0;
          const finalFailedCount = failedCount + loadingCount;

          return {
            ...current,
            updatedAt: new Date().toISOString(),
            turns: current.turns.map((turn) =>
              turn.id === queuedTurn.id
                ? {
                    ...turn,
                    images:
                      loadingCount > 0
                        ? turn.images.map((image) =>
                            image.status === "loading"
                              ? { ...image, status: "error" as const, error: "图片生成未完成" }
                              : image,
                          )
                        : turn.images,
                    status: finalFailedCount > 0 || successCount === 0 ? "error" : "success",
                    error:
                      finalFailedCount > 0
                        ? `其中 ${finalFailedCount} 张未成功生成`
                        : successCount === 0
                          ? "图片生成未完成"
                          : undefined,
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
        const message = normalizeQuotaErrorMessage(error, "生成图片失败");
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
        if (imageQueueRuntime.abortControllers.get(conversationId) === abortController) {
          imageQueueRuntime.abortControllers.delete(conversationId);
        }
        activeConversationQueueIds.delete(conversationId);
      }
    },
    [imageConcurrencyLimit, imageQueueRuntime, isCurrentImageQueueOwner, loadQuota, sessionKey, updateConversation, userId],
  );

  const drainConversationQueues = useCallback(async () => {
    if (imageQueueRuntime.drainInProgress) {
      return;
    }

    imageQueueRuntime.drainInProgress = true;
    try {
      while (imageQueueRuntime.isActive && imageQueueRuntime.sessionKey === sessionKey) {
        const nextConversation = imageQueueRuntime.conversations.find(
          (conversation) =>
            !imageQueueRuntime.activeConversationQueueIds.has(conversation.id) &&
            conversation.turns.some((turn) => turn.status === "queued"),
        );
        if (!nextConversation) {
          return;
        }

        await runConversationQueue(nextConversation.id);
      }
    } finally {
      if (imageQueueRuntime.sessionKey === sessionKey) {
        imageQueueRuntime.drainInProgress = false;
      }
    }
  }, [imageQueueRuntime, runConversationQueue, sessionKey]);

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
      ? (conversationsRef.current.find((conversation) => conversation.id === selectedConversationId) ?? null)
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
      <section className="relative mx-auto flex h-[calc(100dvh-6.75rem)] min-h-0 w-full max-w-[1180px] flex-col overflow-hidden px-3 pb-0 sm:h-[calc(100vh-6.75rem)] sm:px-5 sm:pb-4 lg:h-[calc(100vh-8.5rem)]">
        <div className="pointer-events-none absolute inset-x-4 top-2 -z-10 h-40 rounded-full bg-[radial-gradient(circle_at_center,rgba(214,211,209,0.55),transparent_70%)] blur-3xl" />
        <div className="flex shrink-0 items-center justify-between gap-3 py-2 sm:py-3">
          <div className="min-w-0">
            <h1 className="truncate text-base font-semibold tracking-tight text-stone-950 sm:text-xl">生成图片</h1>
            <div className="mt-0.5 truncate text-[10px] font-bold text-teal-700 md:hidden">
              每张图片消耗 {formatImageCostGgb(1)}
            </div>
          </div>
          <div className="hidden shrink-0 rounded-full bg-teal-50 px-3 py-2 text-xs font-bold text-teal-700 ring-1 ring-teal-100 md:block">
            每张图片消耗 {formatImageCostGgb(1)}
          </div>
          <div className="flex shrink-0 items-center gap-2">
            <div className="max-w-[42vw] rounded-2xl bg-white/85 px-2 py-1.5 text-[10px] font-medium text-stone-600 shadow-sm ring-1 ring-stone-200 sm:max-w-[340px] sm:px-3 sm:py-2 sm:text-xs">
              <div className="truncate font-bold text-stone-900">{accountDisplayName}</div>
              <div className="mt-0.5 truncate">
                {isAdmin ? "上游额度" : "狗狗币余额"} {availableQuota} · {membershipLevelLabel}
              </div>
            </div>
            {activeTaskCount > 0 ? (
              <div className="hidden items-center gap-1.5 rounded-full bg-amber-50 px-3 py-2 text-xs font-medium text-amber-700 ring-1 ring-amber-100 sm:flex">
                <LoaderCircle className="size-3.5 animate-spin" />
                {activeTaskCount} 个任务
              </div>
            ) : null}
            <Button
              variant="outline"
              className="h-9 rounded-full border-stone-200 bg-white/85 px-3 text-stone-700 shadow-sm sm:h-10 sm:px-4"
              onClick={() => setIsHistoryOpen(true)}
              aria-label="打开历史记录"
            >
              <History className="size-4 sm:mr-2" />
              <span className="hidden sm:inline">历史</span>
              <span className="ml-1 text-xs text-stone-400 sm:ml-2">{conversations.length}</span>
            </Button>
            <Button
              className="h-9 rounded-full bg-stone-950 px-3 text-white shadow-sm hover:bg-stone-800 sm:h-10 sm:px-4"
              onClick={handleCreateDraft}
              aria-label="新建图片对话"
            >
              <Plus className="size-4 sm:mr-2" />
              <span className="hidden sm:inline">新建</span>
            </Button>
          </div>
        </div>

        <div
          ref={resultsViewportRef}
          className="hide-scrollbar -mx-3 min-h-0 flex-1 overscroll-contain overflow-y-auto bg-stone-50/50 px-3 py-3 sm:mx-0 sm:rounded-[34px] sm:border sm:border-stone-200/70 sm:px-5 sm:py-6"
        >
          <ImageResults
            selectedConversation={selectedConversation}
            onOpenLightbox={openLightbox}
            onContinueEdit={handleContinueEdit}
            formatConversationTime={formatConversationTime}
            onUsePrompt={handleUsePrompt}
          />
        </div>

        <div className="relative z-20 shrink-0 pt-2 sm:pt-3">
          <ImageComposer
            mode={imageMode}
            prompt={imagePrompt}
            imageCount={imageCount}
            imageSize={imageSize}
            availableQuota={availableQuota}
            availableQuotaLabel={isAdmin ? "上游额度" : "狗狗币余额"}
            estimatedUsageLabel={isAdmin ? "预计生成" : "预计消耗"}
            estimatedUsageValue={isAdmin ? `${parsedCount} 张` : formatImageCostGgb(imageCount)}
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
            reserveMobileBottomNav={!isAdmin}
          />
        </div>
      </section>

      <Dialog open={isHistoryOpen} onOpenChange={setIsHistoryOpen}>
        <DialogContent className="flex h-[min(82dvh,720px)] w-[calc(100vw-1rem)] max-w-[440px] flex-col overflow-hidden rounded-[32px] border-stone-200 bg-white p-0 shadow-2xl sm:w-[92vw]">
          <DialogHeader className="px-6 pt-6 pb-2">
            <DialogTitle className="flex items-center gap-2 text-lg font-bold">
              <History className="size-5" />
              历史记录
            </DialogTitle>
            <DialogDescription className="sr-only">选择历史图片对话，或创建新的图片对话。</DialogDescription>
          </DialogHeader>
          <div className="min-h-0 flex-1 overflow-y-auto px-3 pb-[calc(1rem+env(safe-area-inset-bottom))] sm:px-4 sm:pb-6">
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
              <DialogDescription className="text-sm leading-6">{deleteConfirmDescription}</DialogDescription>
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

  return (
    <ImagePageContent
      key={userId}
      isAdmin={session.role === "admin"}
      userId={userId}
      sessionKey={session.key}
      sessionName={session.name}
    />
  );
}
