"use client";

import localforage from "localforage";

import type { ImageModel } from "@/lib/api";
import { getScopedImagePreferenceStorageKey } from "@/store/image-conversation-scope";
import type { StoredReferenceImage } from "@/store/image-conversations";

export type ImageEditHandoffSource = {
  galleryId: string;
  prompt?: string;
  model?: ImageModel | string;
  size?: string;
};

export type ImageEditHandoff = {
  image: StoredReferenceImage;
  source: ImageEditHandoffSource;
  createdAt: string;
};

const IMAGE_EDIT_HANDOFF_STORAGE_KEY = "image_edit_handoff";

const imageEditHandoffStorage = localforage.createInstance({
  name: "genapi",
  storeName: "image_edit_handoff",
});

export function getImageEditHandoffStorageKey(ownerId: string) {
  return getScopedImagePreferenceStorageKey(IMAGE_EDIT_HANDOFF_STORAGE_KEY, ownerId);
}

function normalizeHandoff(value: unknown): ImageEditHandoff | null {
  if (!value || typeof value !== "object") {
    return null;
  }

  const candidate = value as Partial<ImageEditHandoff>;
  const image = candidate.image;
  const source = candidate.source;
  if (!image || typeof image.dataUrl !== "string" || !image.dataUrl) {
    return null;
  }
  if (!source || typeof source.galleryId !== "string" || !source.galleryId) {
    return null;
  }

  return {
    image: {
      name: image.name || "gallery-reference.png",
      type: image.type || "image/png",
      dataUrl: image.dataUrl,
    },
    source: {
      galleryId: source.galleryId,
      prompt: source.prompt,
      model: source.model,
      size: source.size,
    },
    createdAt: typeof candidate.createdAt === "string" ? candidate.createdAt : new Date().toISOString(),
  };
}

export async function saveImageEditHandoff(
  ownerId: string,
  image: StoredReferenceImage,
  source: ImageEditHandoffSource,
) {
  const handoff = normalizeHandoff({
    image,
    source,
    createdAt: new Date().toISOString(),
  });
  if (!handoff) {
    throw new Error("invalid image edit handoff");
  }
  await imageEditHandoffStorage.setItem(getImageEditHandoffStorageKey(ownerId), handoff);
}

export async function consumeImageEditHandoff(ownerId: string): Promise<ImageEditHandoff | null> {
  const key = getImageEditHandoffStorageKey(ownerId);
  const item = await imageEditHandoffStorage.getItem<unknown>(key);
  await imageEditHandoffStorage.removeItem(key);
  return normalizeHandoff(item);
}
