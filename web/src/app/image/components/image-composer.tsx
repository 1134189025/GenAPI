"use client";
/* eslint-disable @next/next/no-img-element -- Reference previews are local data URLs. */
import { ArrowUp, Check, ChevronDown, ImagePlus, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState, type ClipboardEvent, type RefObject } from "react";

import { ImageLightbox } from "@/components/image-lightbox";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { formatImageCostGgb } from "@/lib/ggb";
import { cn } from "@/lib/utils";
import type { ImageConversationMode } from "@/store/image-conversations";

type ImageComposerProps = {
  mode: ImageConversationMode;
  prompt: string;
  imageCount: string;
  imageSize: string;
  availableQuota: string;
  availableQuotaLabel: string;
  estimatedUsageLabel?: string;
  estimatedUsageValue?: string;
  activeTaskCount: number;
  referenceImages: Array<{ name: string; dataUrl: string }>;
  textareaRef: RefObject<HTMLTextAreaElement | null>;
  fileInputRef: RefObject<HTMLInputElement | null>;
  onModeChange: (value: ImageConversationMode) => void;
  onPromptChange: (value: string) => void;
  onImageCountChange: (value: string) => void;
  onImageSizeChange: (value: string) => void;
  onSubmit: () => void | Promise<void>;
  onPickReferenceImage: () => void;
  onReferenceImageChange: (files: File[]) => void | Promise<void>;
  onRemoveReferenceImage: (index: number) => void;
  reserveMobileBottomNav: boolean;
};

const imageSizeOptions = [
  { value: "", label: "未指定" },
  { value: "1024x1024", label: "1024x1024 正方形" },
  { value: "1536x864", label: "1536x864 横版" },
  { value: "864x1536", label: "864x1536 竖版" },
  { value: "1280x960", label: "1280x960 横版" },
  { value: "960x1280", label: "960x1280 竖版" },
];

export function ImageComposer({
  mode,
  prompt,
  imageCount,
  imageSize,
  availableQuota,
  availableQuotaLabel,
  estimatedUsageLabel = "预计消耗",
  estimatedUsageValue,
  activeTaskCount,
  referenceImages,
  textareaRef,
  fileInputRef,
  onModeChange,
  onPromptChange,
  onImageCountChange,
  onImageSizeChange,
  onSubmit,
  onPickReferenceImage,
  onReferenceImageChange,
  onRemoveReferenceImage,
  reserveMobileBottomNav,
}: ImageComposerProps) {
  const [lightboxOpen, setLightboxOpen] = useState(false);
  const [lightboxIndex, setLightboxIndex] = useState(0);
  const [isSizeMenuOpen, setIsSizeMenuOpen] = useState(false);
  const [isMoreSettingsOpen, setIsMoreSettingsOpen] = useState(false);
  const sizeMenuRef = useRef<HTMLDivElement>(null);
  const lightboxImages = useMemo(
    () => referenceImages.map((image, index) => ({ id: `${image.name}-${index}`, src: image.dataUrl })),
    [referenceImages],
  );
  const imageSizeLabel = imageSizeOptions.find((option) => option.value === imageSize)?.label || "未指定";
  const estimatedUsage = estimatedUsageValue || formatImageCostGgb(imageCount);

  useEffect(() => {
    const textarea = textareaRef.current;
    if (!textarea) {
      return;
    }

    const resizeTextarea = () => {
      const isDesktop = typeof window !== "undefined" && window.matchMedia("(min-width: 640px)").matches;
      const viewportHeight = typeof window !== "undefined" ? window.innerHeight : 844;
      const maxHeight = Math.round(viewportHeight * (isDesktop ? 0.32 : 0.18));

      textarea.style.height = "auto";
      textarea.style.height = `${Math.min(textarea.scrollHeight, maxHeight)}px`;
    };

    resizeTextarea();
    window.addEventListener("resize", resizeTextarea);
    return () => {
      window.removeEventListener("resize", resizeTextarea);
    };
  }, [prompt, textareaRef]);

  useEffect(() => {
    if (!isSizeMenuOpen) {
      return;
    }
    const handlePointerDown = (event: MouseEvent) => {
      if (!sizeMenuRef.current?.contains(event.target as Node)) {
        setIsSizeMenuOpen(false);
      }
    };
    window.addEventListener("mousedown", handlePointerDown);
    return () => {
      window.removeEventListener("mousedown", handlePointerDown);
    };
  }, [isSizeMenuOpen]);

  const handleTextareaPaste = (event: ClipboardEvent<HTMLTextAreaElement>) => {
    const imageFiles = Array.from(event.clipboardData.files).filter((file) => file.type.startsWith("image/"));
    if (imageFiles.length === 0) {
      return;
    }

    event.preventDefault();
    void onReferenceImageChange(imageFiles);
  };

  return (
    <div
      className={cn(
        "shrink-0 px-0 sm:px-1",
        reserveMobileBottomNav ? "pb-[calc(3.75rem+env(safe-area-inset-bottom))] lg:pb-2" : "pb-2",
      )}
    >
      <div className="mx-auto w-full max-w-[920px]">
        {mode === "edit" ? (
          <input
            ref={fileInputRef}
            type="file"
            accept="image/*"
            multiple
            className="hidden"
            onChange={(event) => {
              void onReferenceImageChange(Array.from(event.target.files || []));
            }}
          />
        ) : null}

        {mode === "edit" && referenceImages.length > 0 ? (
          <div className="mb-2 flex max-h-[3.75rem] flex-nowrap gap-2 overflow-x-auto overscroll-x-contain px-1 pb-1 sm:mb-3 sm:max-h-[9rem] sm:flex-wrap sm:overflow-x-hidden sm:overflow-y-auto sm:overscroll-contain">
            {referenceImages.map((image, index) => (
              <div key={`${image.name}-${index}`} className="relative size-14 flex-none sm:size-16">
                <button
                  type="button"
                  onClick={() => {
                    setLightboxIndex(index);
                    setLightboxOpen(true);
                  }}
                  className="group size-14 overflow-hidden rounded-2xl border border-stone-200 bg-stone-50 transition hover:border-stone-300 sm:size-16"
                  aria-label={`预览参考图 ${image.name || index + 1}`}
                >
                  <img
                    src={image.dataUrl}
                    alt={image.name || `参考图 ${index + 1}`}
                    className="h-full w-full object-cover"
                  />
                </button>
                <button
                  type="button"
                  onClick={(event) => {
                    event.stopPropagation();
                    onRemoveReferenceImage(index);
                  }}
                  className="absolute right-1 top-1 inline-flex size-6 items-center justify-center rounded-full border border-stone-200 bg-white text-stone-500 shadow-sm transition hover:border-stone-300 hover:text-stone-800 sm:size-5"
                  aria-label={`移除参考图 ${image.name || index + 1}`}
                >
                  <X className="size-3" />
                </button>
              </div>
            ))}
          </div>
        ) : null}

        <div className="rounded-[22px] border border-stone-200 bg-white/95 shadow-[0_12px_36px_-30px_rgba(28,25,23,0.42)] backdrop-blur sm:rounded-[30px] sm:shadow-[0_18px_60px_-36px_rgba(28,25,23,0.45)]">
          <div
            className="relative cursor-text"
            onClick={() => {
              textareaRef.current?.focus();
            }}
          >
            <ImageLightbox
              images={lightboxImages}
              currentIndex={lightboxIndex}
              open={lightboxOpen}
              onOpenChange={setLightboxOpen}
              onIndexChange={setLightboxIndex}
            />
            <Textarea
              ref={textareaRef}
              rows={1}
              value={prompt}
              onChange={(event) => onPromptChange(event.target.value)}
              onPaste={handleTextareaPaste}
              placeholder={
                mode === "edit" ? "描述你希望如何修改这张参考图，可直接粘贴图片" : "输入你想要生成的画面，也可直接粘贴图片"
              }
              onKeyDown={(event) => {
                if (event.key === "Enter" && !event.shiftKey) {
                  event.preventDefault();
                  void onSubmit();
                }
              }}
              className="max-h-[18dvh] min-h-[44px] resize-none overflow-y-auto rounded-[22px] border-0 bg-transparent px-3 pt-2.5 pb-2 text-[15px] leading-6 text-stone-900 shadow-none placeholder:text-stone-400 focus-visible:ring-0 sm:max-h-[32dvh] sm:min-h-[116px] sm:rounded-[30px] sm:px-6 sm:pt-5 sm:leading-7"
            />

            <div className="border-t border-stone-100 px-2.5 py-2.5 sm:px-4 sm:py-3">
              <div className="flex items-end justify-between gap-2 sm:gap-3">
                <div className="flex min-w-0 flex-1 flex-wrap items-center gap-2">
                  <div className="flex items-center gap-1.5 rounded-full bg-stone-100 p-1">
                    <ModeButton active={mode === "generate"} onClick={() => onModeChange("generate")}>
                      文生图
                    </ModeButton>
                    <ModeButton active={mode === "edit"} onClick={() => onModeChange("edit")}>
                      图生图
                    </ModeButton>
                  </div>

                  {mode === "edit" ? (
                    <Button
                      type="button"
                      variant="outline"
                      className="h-8 touch-manipulation rounded-full border-stone-200 bg-white px-2.5 text-xs font-medium text-stone-700 shadow-none sm:h-10 sm:px-4 sm:text-sm"
                      onClick={onPickReferenceImage}
                    >
                      <ImagePlus className="size-3.5 sm:size-4" />
                      <span>{referenceImages.length > 0 ? "加参考图" : "上传参考图"}</span>
                    </Button>
                  ) : null}

                  <div
                    className={cn(
                      "items-center gap-1.5 rounded-full border border-stone-200 bg-white px-2 py-0.5 sm:gap-2 sm:px-3 sm:py-1",
                      isMoreSettingsOpen ? "flex" : "hidden sm:flex",
                    )}
                  >
                    <span className="text-[11px] font-medium text-stone-700 sm:text-sm">张数</span>
                    <Input
                      type="number"
                      min="1"
                      max="10"
                      step="1"
                      value={imageCount}
                      onChange={(event) => onImageCountChange(event.target.value)}
                      className="h-7 w-[40px] border-0 bg-transparent px-0 text-center text-xs font-medium text-stone-700 shadow-none focus-visible:ring-0 sm:h-8 sm:w-[58px] sm:text-sm"
                    />
                  </div>
                  <div
                    ref={sizeMenuRef}
                    className={cn(
                      "relative items-center gap-1.5 rounded-full border border-stone-200 bg-white px-2 py-0.5 text-[11px] sm:gap-2 sm:px-3 sm:py-1 sm:text-[13px]",
                      isMoreSettingsOpen ? "flex" : "hidden sm:flex",
                    )}
                  >
                    <span className="font-medium text-stone-700 sm:text-sm">尺寸</span>
                    <button
                      type="button"
                      className="flex h-7 w-[112px] max-w-[calc(100vw-8rem)] touch-manipulation items-center justify-between bg-transparent text-left text-xs font-bold text-stone-700 sm:h-8 sm:w-[128px] sm:max-w-none"
                      onClick={() => setIsSizeMenuOpen((open) => !open)}
                    >
                      <span className="truncate">{imageSizeLabel}</span>
                      <ChevronDown className={cn("size-4 shrink-0 opacity-60 transition", isSizeMenuOpen && "rotate-180")} />
                    </button>
                    {isSizeMenuOpen ? (
                      <div className="absolute right-0 bottom-[calc(100%+10px)] z-50 max-h-[min(16rem,calc(100dvh-12rem))] w-[min(18rem,calc(100vw-2rem))] overflow-y-auto overscroll-contain rounded-3xl border border-white/80 bg-white p-2 shadow-[0_24px_80px_-32px_rgba(15,23,42,0.35)] sm:right-auto sm:left-0 sm:w-[186px]">
                        {imageSizeOptions.map((option) => {
                          const active = option.value === imageSize;
                          return (
                            <button
                              key={option.label}
                              type="button"
                              className={cn(
                                "flex w-full items-center justify-between rounded-2xl px-3 py-2 text-left text-sm text-stone-700 transition hover:bg-stone-100",
                                active && "bg-stone-100 font-medium text-stone-950",
                              )}
                              onClick={() => {
                                onImageSizeChange(option.value);
                                setIsSizeMenuOpen(false);
                              }}
                            >
                              <span>{option.label}</span>
                              {active ? <Check className="size-4" /> : null}
                            </button>
                          );
                        })}
                      </div>
                    ) : null}
                  </div>

                  <button
                    type="button"
                    className={cn(
                      "touch-manipulation rounded-full px-2.5 py-1.5 text-xs font-medium transition sm:px-3 sm:py-2 sm:text-sm",
                      isMoreSettingsOpen ? "bg-stone-900 text-white" : "bg-stone-100 text-stone-600 hover:bg-stone-200",
                    )}
                    onClick={() => setIsMoreSettingsOpen((open) => !open)}
                  >
                    更多设置
                  </button>
                </div>

                <button
                  type="button"
                  onClick={() => void onSubmit()}
                  disabled={!prompt.trim() || (mode === "edit" && referenceImages.length === 0)}
                  className="inline-flex size-10 shrink-0 touch-manipulation items-center justify-center rounded-full bg-stone-950 text-white transition hover:bg-stone-800 disabled:cursor-not-allowed disabled:bg-stone-300 sm:size-11"
                  aria-label={mode === "edit" ? "编辑图片" : "生成图片"}
                >
                  <ArrowUp className="size-3.5 sm:size-4" />
                </button>
              </div>

              {isMoreSettingsOpen ? (
                <div className="mt-2 grid gap-1.5 rounded-2xl bg-stone-50 px-3 py-2 text-[11px] leading-5 text-stone-600 sm:mt-3 sm:grid-cols-3 sm:gap-2 sm:rounded-3xl sm:px-4 sm:py-3 sm:text-xs">
                  <div>
                    <span className="font-medium text-stone-900">{availableQuotaLabel}</span>
                    <span className="ml-2">{availableQuota}</span>
                  </div>
                  <div>
                    <span className="font-medium text-stone-900">{estimatedUsageLabel}</span>
                    <span className="ml-2">{estimatedUsage}</span>
                  </div>
                  <div>
                    <span className="font-medium text-stone-900">任务</span>
                    <span className="ml-2">{activeTaskCount > 0 ? `${activeTaskCount} 个处理中` : "空闲"}</span>
                  </div>
                </div>
              ) : null}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

function ModeButton({
  active,
  children,
  onClick,
}: {
  active: boolean;
  children: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "touch-manipulation rounded-full px-2.5 py-1.5 text-xs font-medium transition sm:px-3.5 sm:py-2 sm:text-sm",
        active ? "bg-white text-stone-950 shadow-sm" : "text-stone-500 hover:text-stone-900",
      )}
    >
      {children}
    </button>
  );
}
