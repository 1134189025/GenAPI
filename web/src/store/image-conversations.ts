"use client";

import localforage from "localforage";

import type { ImageModel } from "@/lib/api";
import {
  getImageConversationDeletedStorageKey,
  getImageConversationStorageKey,
} from "@/store/image-conversation-scope";

export type ImageConversationMode = "generate" | "edit";

export type StoredReferenceImage = {
  name: string;
  type: string;
  dataUrl: string;
};

export type StoredImage = {
  id: string;
  status?: "loading" | "success" | "error";
  b64_json?: string;
  serverId?: string;
  url?: string;
  expiresAt?: string;
  width?: number;
  height?: number;
  targetSize?: string;
  targetWidth?: number | null;
  targetHeight?: number | null;
  targetAspectRatio?: string;
  error?: string;
};

export type ImageTurnStatus = "queued" | "generating" | "success" | "error";

export type ImageTurn = {
  id: string;
  prompt: string;
  model: ImageModel;
  mode: ImageConversationMode;
  referenceImages: StoredReferenceImage[];
  count: number;
  size: string;
  images: StoredImage[];
  createdAt: string;
  status: ImageTurnStatus;
  error?: string;
};

export type ImageConversation = {
  id: string;
  title: string;
  createdAt: string;
  updatedAt: string;
  turns: ImageTurn[];
};

export type ImageConversationStats = {
  queued: number;
  running: number;
};

const imageConversationStorage = localforage.createInstance({
  name: "genapi",
  storeName: "image_conversations",
});

let imageConversationWriteQueue: Promise<void> = Promise.resolve();

function normalizeOptionalNumber(value: unknown): number | undefined {
  const numberValue = Number(value);
  return Number.isFinite(numberValue) && numberValue > 0 ? numberValue : undefined;
}

function normalizeOptionalNullableNumber(value: unknown): number | null | undefined {
  if (value === null) {
    return null;
  }
  return normalizeOptionalNumber(value);
}

function normalizeStoredImage(image: StoredImage): StoredImage {
  const source = image as StoredImage & Record<string, unknown>;
  const normalized: StoredImage = {
    ...image,
    width: normalizeOptionalNumber(source.width),
    height: normalizeOptionalNumber(source.height),
    targetSize: typeof source.targetSize === "string" && source.targetSize ? source.targetSize : undefined,
    targetWidth: normalizeOptionalNullableNumber(source.targetWidth),
    targetHeight: normalizeOptionalNullableNumber(source.targetHeight),
    targetAspectRatio:
      typeof source.targetAspectRatio === "string" && source.targetAspectRatio ? source.targetAspectRatio : undefined,
  };
  if (normalized.status === "loading" || normalized.status === "error" || normalized.status === "success") {
    return normalized;
  }
  return {
    ...normalized,
    status: normalized.b64_json ? "success" : "loading",
  };
}

function normalizeReferenceImage(image: StoredReferenceImage): StoredReferenceImage {
  return {
    name: image.name || "reference.png",
    type: image.type || "image/png",
    dataUrl: image.dataUrl,
  };
}

function dataUrlMimeType(dataUrl: string) {
  const match = dataUrl.match(/^data:(.*?);base64,/);
  return match?.[1] || "image/png";
}

function getLegacyReferenceImages(source: Record<string, unknown>): StoredReferenceImage[] {
  if (Array.isArray(source.referenceImages)) {
    return source.referenceImages
      .filter((image): image is StoredReferenceImage => {
        if (!image || typeof image !== "object") {
          return false;
        }
        const candidate = image as StoredReferenceImage;
        return typeof candidate.dataUrl === "string" && candidate.dataUrl.length > 0;
      })
      .map(normalizeReferenceImage);
  }

  if (source.sourceImage && typeof source.sourceImage === "object") {
    const image = source.sourceImage as { dataUrl?: unknown; fileName?: unknown };
    if (typeof image.dataUrl === "string" && image.dataUrl) {
      return [
        {
          name: typeof image.fileName === "string" && image.fileName ? image.fileName : "reference.png",
          type: dataUrlMimeType(image.dataUrl),
          dataUrl: image.dataUrl,
        },
      ];
    }
  }

  return [];
}

function normalizeTurn(turn: ImageTurn & Record<string, unknown>): ImageTurn {
  const normalizedImages = Array.isArray(turn.images) ? turn.images.map(normalizeStoredImage) : [];
  const derivedStatus: ImageTurnStatus =
    normalizedImages.some((image) => image.status === "loading")
      ? "generating"
      : normalizedImages.some((image) => image.status === "error")
        ? "error"
        : "success";

  return {
    id: String(turn.id || `${Date.now()}`),
    prompt: String(turn.prompt || ""),
    model: (turn.model as ImageModel) || "gpt-image-2",
    mode: turn.mode === "edit" ? "edit" : "generate",
    referenceImages: getLegacyReferenceImages(turn),
    count: Math.max(1, Number(turn.count || normalizedImages.length || 1)),
    size: typeof turn.size === "string" ? turn.size : "",
    images: normalizedImages,
    createdAt: String(turn.createdAt || new Date().toISOString()),
    status:
      turn.status === "queued" ||
      turn.status === "generating" ||
      turn.status === "success" ||
      turn.status === "error"
        ? turn.status
        : derivedStatus,
    error: typeof turn.error === "string" ? turn.error : undefined,
  };
}

function normalizeConversation(conversation: ImageConversation & Record<string, unknown>): ImageConversation {
  const turns = Array.isArray(conversation.turns)
    ? conversation.turns.map((turn) => normalizeTurn(turn as ImageTurn & Record<string, unknown>))
    : [
        normalizeTurn({
          id: String(conversation.id || `${Date.now()}`),
          prompt: String(conversation.prompt || ""),
          model: (conversation.model as ImageModel) || "gpt-image-2",
          mode: conversation.mode === "edit" ? "edit" : "generate",
          referenceImages: getLegacyReferenceImages(conversation),
          count: Number(conversation.count || 1),
          size: typeof conversation.size === "string" ? conversation.size : "",
          images: Array.isArray(conversation.images) ? (conversation.images as StoredImage[]) : [],
          createdAt: String(conversation.createdAt || new Date().toISOString()),
          status:
            conversation.status === "generating" || conversation.status === "success" || conversation.status === "error"
              ? conversation.status
              : "success",
          error: typeof conversation.error === "string" ? conversation.error : undefined,
        }),
      ];
  const lastTurn = turns.length > 0 ? turns[turns.length - 1] : null;

  return {
    id: String(conversation.id || `${Date.now()}`),
    title: String(conversation.title || ""),
    createdAt: String(conversation.createdAt || lastTurn?.createdAt || new Date().toISOString()),
    updatedAt: String(conversation.updatedAt || lastTurn?.createdAt || new Date().toISOString()),
    turns,
  };
}

function sortImageConversations(conversations: ImageConversation[]): ImageConversation[] {
  return [...conversations].sort((a, b) => b.updatedAt.localeCompare(a.updatedAt));
}

function getTimestamp(value: string) {
  const time = new Date(value).getTime();
  return Number.isFinite(time) ? time : 0;
}

function pickLatestConversation(current: ImageConversation, next: ImageConversation) {
  return getTimestamp(next.updatedAt) >= getTimestamp(current.updatedAt) ? next : current;
}

function queueImageConversationWrite<T>(operation: () => Promise<T>): Promise<T> {
  const result = imageConversationWriteQueue.then(operation);
  imageConversationWriteQueue = result.then(
    () => undefined,
    () => undefined,
  );
  return result;
}

async function readStoredImageConversations(ownerId: string): Promise<ImageConversation[]> {
  const items =
    (await imageConversationStorage.getItem<Array<ImageConversation & Record<string, unknown>>>(
      getImageConversationStorageKey(ownerId),
    )) || [];
  return items.map(normalizeConversation);
}

async function readDeletedImageConversationIds(ownerId: string): Promise<Set<string>> {
  const ids = (await imageConversationStorage.getItem<unknown[]>(getImageConversationDeletedStorageKey(ownerId))) || [];
  return new Set(ids.filter((id): id is string => typeof id === "string" && id.length > 0));
}

async function persistDeletedImageConversationIds(ownerId: string, ids: Set<string>): Promise<void> {
  await imageConversationStorage.setItem(getImageConversationDeletedStorageKey(ownerId), [...ids]);
}

function filterDeletedImageConversations(conversations: ImageConversation[], deletedIds: Set<string>) {
  if (deletedIds.size === 0) {
    return conversations;
  }
  return conversations.filter((conversation) => !deletedIds.has(conversation.id));
}

export async function listImageConversations(ownerId: string): Promise<ImageConversation[]> {
  const [items, deletedIds] = await Promise.all([
    readStoredImageConversations(ownerId),
    readDeletedImageConversationIds(ownerId),
  ]);
  return sortImageConversations(filterDeletedImageConversations(items, deletedIds));
}

export async function saveImageConversations(ownerId: string, conversations: ImageConversation[]): Promise<void> {
  await queueImageConversationWrite(async () => {
    const [items, deletedIds] = await Promise.all([
      readStoredImageConversations(ownerId),
      readDeletedImageConversationIds(ownerId),
    ]);
    const activeItems = filterDeletedImageConversations(items, deletedIds);
    const conversationMap = new Map(activeItems.map((item) => [item.id, item]));
    for (const conversation of conversations.map(normalizeConversation)) {
      if (deletedIds.has(conversation.id)) {
        continue;
      }
      const current = conversationMap.get(conversation.id);
      conversationMap.set(conversation.id, current ? pickLatestConversation(current, conversation) : conversation);
    }
    await imageConversationStorage.setItem(
      getImageConversationStorageKey(ownerId),
      sortImageConversations([...conversationMap.values()]),
    );
  });
}

export async function saveImageConversation(ownerId: string, conversation: ImageConversation): Promise<void> {
  await queueImageConversationWrite(async () => {
    const [items, deletedIds] = await Promise.all([
      readStoredImageConversations(ownerId),
      readDeletedImageConversationIds(ownerId),
    ]);
    const nextConversation = normalizeConversation(conversation);
    const activeItems = filterDeletedImageConversations(items, deletedIds);
    if (deletedIds.has(nextConversation.id)) {
      if (activeItems.length !== items.length) {
        await imageConversationStorage.setItem(getImageConversationStorageKey(ownerId), activeItems);
      }
      return;
    }
    const current = activeItems.find((item) => item.id === nextConversation.id);
    const persistedConversation = current ? pickLatestConversation(current, nextConversation) : nextConversation;
    const nextItems = sortImageConversations([
      persistedConversation,
      ...activeItems.filter((item) => item.id !== persistedConversation.id),
    ]);
    await imageConversationStorage.setItem(getImageConversationStorageKey(ownerId), nextItems);
  });
}

export async function deleteImageConversation(ownerId: string, id: string): Promise<void> {
  await queueImageConversationWrite(async () => {
    const [items, deletedIds] = await Promise.all([
      readStoredImageConversations(ownerId),
      readDeletedImageConversationIds(ownerId),
    ]);
    deletedIds.add(id);
    await persistDeletedImageConversationIds(ownerId, deletedIds);
    await imageConversationStorage.setItem(
      getImageConversationStorageKey(ownerId),
      items.filter((item) => item.id !== id),
    );
  });
}

export async function clearImageConversations(ownerId: string): Promise<void> {
  await queueImageConversationWrite(async () => {
    const [items, deletedIds] = await Promise.all([
      readStoredImageConversations(ownerId),
      readDeletedImageConversationIds(ownerId),
    ]);
    for (const item of items) {
      deletedIds.add(item.id);
    }
    await persistDeletedImageConversationIds(ownerId, deletedIds);
    await imageConversationStorage.removeItem(getImageConversationStorageKey(ownerId));
  });
}

export function getImageConversationStats(conversation: ImageConversation | null): ImageConversationStats {
  if (!conversation) {
    return { queued: 0, running: 0 };
  }

  return conversation.turns.reduce(
    (acc, turn) => {
      if (turn.status === "queued") {
        acc.queued += 1;
      } else if (turn.status === "generating") {
        acc.running += 1;
      }
      return acc;
    },
    { queued: 0, running: 0 },
  );
}
