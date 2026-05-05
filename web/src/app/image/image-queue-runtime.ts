import type { ImageConversation } from "@/store/image-conversations";

export const IMAGE_QUEUE_UPDATED_EVENT = "genapi:image-queue-updated";

export type ImageQueueRuntime = {
  sessionKey: string;
  userId: string;
  conversations: ImageConversation[];
  activeConversationQueueIds: Set<string>;
  abortControllers: Map<string, AbortController>;
  drainInProgress: boolean;
  isActive: boolean;
  listeners: Set<(items: ImageConversation[]) => void>;
};

const imageQueueRuntimes = new Map<string, ImageQueueRuntime>();

export function getImageQueueRuntime(sessionKey: string): ImageQueueRuntime {
  const normalizedSessionKey = String(sessionKey || "").trim();
  const current = imageQueueRuntimes.get(normalizedSessionKey);
  if (current) {
    return current;
  }
  const runtime: ImageQueueRuntime = {
    sessionKey: normalizedSessionKey,
    userId: "",
    conversations: [],
    activeConversationQueueIds: new Set<string>(),
    abortControllers: new Map<string, AbortController>(),
    drainInProgress: false,
    isActive: true,
    listeners: new Set(),
  };
  imageQueueRuntimes.set(normalizedSessionKey, runtime);
  return runtime;
}

export function notifyImageQueueRuntime(runtime: ImageQueueRuntime) {
  const items = [...runtime.conversations];
  runtime.listeners.forEach((listener) => listener(items));
  if (typeof window !== "undefined") {
    window.dispatchEvent(new CustomEvent(IMAGE_QUEUE_UPDATED_EVENT, { detail: { sessionKey: runtime.sessionKey } }));
  }
}

export function hasLiveImageQueueWork(sessionKey: string) {
  const runtime = imageQueueRuntimes.get(String(sessionKey || "").trim());
  return Boolean(
    runtime?.isActive &&
      (runtime.abortControllers.size > 0 ||
        runtime.activeConversationQueueIds.size > 0 ||
        runtime.drainInProgress ||
        runtime.conversations.some((conversation) =>
          conversation.turns.some((turn) => turn.status === "queued" || turn.status === "generating"),
        )),
  );
}

function deactivateImageQueueRuntime(runtime: ImageQueueRuntime) {
  runtime.abortControllers.forEach((controller) => controller.abort());
  runtime.abortControllers.clear();
  runtime.drainInProgress = false;
  runtime.isActive = false;
}

export function deactivateAllImageQueueRuntimes() {
  imageQueueRuntimes.forEach((runtime) => deactivateImageQueueRuntime(runtime));
}

export function deactivateStaleImageQueueRuntimes(activeSessionKey: string) {
  const normalizedSessionKey = String(activeSessionKey || "").trim();
  imageQueueRuntimes.forEach((runtime) => {
    if (!normalizedSessionKey || runtime.sessionKey !== normalizedSessionKey) {
      deactivateImageQueueRuntime(runtime);
    }
  });
}
