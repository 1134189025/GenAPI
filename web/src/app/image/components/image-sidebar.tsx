"use client";

import { LoaderCircle, MessageSquarePlus, Trash2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { getImageConversationStats, type ImageConversation } from "@/store/image-conversations";

type ImageSidebarProps = {
  conversations: ImageConversation[];
  isLoadingHistory: boolean;
  selectedConversationId: string | null;
  onCreateDraft: () => void;
  onClearHistory: () => void | Promise<void>;
  onSelectConversation: (id: string) => void;
  onDeleteConversation: (id: string) => void | Promise<void>;
  formatConversationTime: (value: string) => string;
  hideActionButtons?: boolean;
};

export function ImageSidebar({
  conversations,
  isLoadingHistory,
  selectedConversationId,
  onCreateDraft,
  onClearHistory,
  onSelectConversation,
  onDeleteConversation,
  formatConversationTime,
  hideActionButtons = false,
}: ImageSidebarProps) {
  return (
    <aside className="h-full min-h-0 overflow-hidden">
      <div className="flex h-full min-h-0 flex-col gap-2 py-1 sm:gap-3 sm:py-2">
        {!hideActionButtons ? (
          <div className="flex items-center gap-2">
            <Button className="h-11 flex-1 rounded-2xl bg-primary text-primary-foreground shadow-lg shadow-primary/20 transition hover:bg-primary/90" onClick={onCreateDraft}>
              <MessageSquarePlus className="size-4" />
              新建创作
            </Button>
            <Button
              variant="ghost"
              className="h-11 rounded-2xl bg-black/5 px-4 text-foreground/60 hover:bg-black/10 dark:bg-white/5 dark:hover:bg-white/10"
              onClick={() => void onClearHistory()}
              disabled={conversations.length === 0}
              aria-label="清空历史记录"
            >
              <Trash2 className="size-4" />
            </Button>
          </div>
        ) : null}

        <div className="min-h-0 flex-1 space-y-2 overflow-y-auto pr-1 hide-scrollbar">
          {isLoadingHistory ? (
            <div className="flex items-center gap-3 px-3 py-4 text-sm font-medium text-muted-foreground">
              <LoaderCircle className="size-4 animate-spin" />
              读取历史中...
            </div>
          ) : conversations.length === 0 ? (
            <div className="px-3 py-6 text-center text-sm font-medium leading-relaxed text-muted-foreground">还没有图片记录</div>
          ) : (
            conversations.map((conversation) => {
              const active = conversation.id === selectedConversationId;
              const stats = getImageConversationStats(conversation);
              return (
                <div
                  key={conversation.id}
                  className={cn(
                    "group relative w-full overflow-hidden rounded-2xl p-4 text-left transition-all duration-200",
                    active
                      ? "bg-primary/5 text-primary ring-1 ring-primary/10"
                      : "text-foreground/70 hover:bg-black/5 dark:hover:bg-white/5",
                  )}
                >
                  <button
                    type="button"
                    onClick={() => onSelectConversation(conversation.id)}
                    className="block w-full pr-12 sm:pr-8 text-left"
                  >
                    <div className="truncate text-sm font-bold tracking-tight">
                      <span className="truncate">{conversation.title}</span>
                    </div>
                    <div className={cn("mt-1 text-xs font-medium", active ? "text-primary/70" : "text-muted-foreground")}>
                      {conversation.turns.length} 次创作 · {formatConversationTime(conversation.updatedAt)}
                    </div>
                    {stats.running > 0 || stats.queued > 0 ? (
                      <div className="mt-2 flex flex-wrap items-center gap-2 text-xs">
                        {stats.running > 0 ? (
                          <span className="rounded-full bg-blue-100 px-2 py-1 font-bold text-blue-700">处理中 {stats.running}</span>
                        ) : null}
                        {stats.queued > 0 ? (
                          <span className="rounded-full bg-amber-100 px-2 py-1 font-bold text-amber-700">排队 {stats.queued}</span>
                        ) : null}
                      </div>
                    ) : null}
                  </button>
                  <button
                    type="button"
                    onClick={() => void onDeleteConversation(conversation.id)}
                    className="absolute top-1/2 right-2 flex size-10 -translate-y-1/2 touch-manipulation items-center justify-center rounded-xl text-foreground/55 opacity-100 transition-all hover:bg-destructive/10 hover:text-destructive sm:right-3 sm:size-8 sm:opacity-0 sm:group-hover:opacity-100"
                    aria-label="删除会话"
                  >
                    <Trash2 className="size-4" />
                  </button>
                </div>
              );
            })
          )}
        </div>
      </div>
    </aside>
  );
}
