"use client";
/* eslint-disable @next/next/no-img-element -- Generated images and references are local data URLs. */

import { useState } from "react";
import { Clock3, Download, LoaderCircle, Sparkles } from "lucide-react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import type { ImageConversation, ImageTurnStatus, StoredImage, StoredReferenceImage } from "@/store/image-conversations";

export type ImageLightboxItem = {
  id: string;
  src: string;
  sizeLabel?: string;
  dimensions?: string;
};

type ImageResultsProps = {
  selectedConversation: ImageConversation | null;
  onOpenLightbox: (images: ImageLightboxItem[], index: number) => void;
  onContinueEdit: (conversationId: string, image: StoredImage | StoredReferenceImage) => void;
  formatConversationTime: (value: string) => string;
  onUsePrompt?: (prompt: string) => void;
};

export function ImageResults({
  selectedConversation,
  onOpenLightbox,
  onContinueEdit,
  formatConversationTime,
  onUsePrompt,
}: ImageResultsProps) {
  const [imageDimensions, setImageDimensions] = useState<Record<string, string>>({});

  const updateImageDimensions = (id: string, width: number, height: number) => {
    const dimensions = formatImageDimensions(width, height);
    setImageDimensions((current) => {
      if (current[id] === dimensions) {
        return current;
      }
      return { ...current, [id]: dimensions };
    });
  };

  if (!selectedConversation) {
    return (
      <div className="flex h-full min-h-[420px] items-center justify-center px-2 text-center">
        <div className="w-full max-w-3xl">
          <p className="mb-3 text-xs font-semibold uppercase tracking-[0.28em] text-stone-400">Image Studio</p>
          <h1 className="text-3xl font-semibold tracking-tight text-stone-950 sm:text-5xl">
            想生成什么图片？
          </h1>
          <p className="mx-auto mt-4 max-w-xl text-sm leading-7 text-stone-500 sm:text-base">
            输入一句描述就能开始。需要改图时，直接粘贴或上传参考图。
          </p>
          <div className="mt-8 grid gap-3 text-left sm:grid-cols-3">
            {promptIdeas.map((item) => (
              <button
                key={item.title}
                type="button"
                className="rounded-3xl border border-stone-200 bg-white/85 px-4 py-4 text-left transition hover:-translate-y-0.5 hover:border-stone-300 hover:bg-white hover:shadow-lg"
                onClick={() => onUsePrompt?.(item.prompt)}
              >
                <div className="text-sm font-semibold text-stone-950">{item.title}</div>
                <div className="mt-2 text-xs leading-5 text-stone-500">{item.prompt}</div>
              </button>
            ))}
          </div>
          <p className="mt-5 text-xs text-stone-400">试试这些提示，也可以直接写自己的想法。</p>
        </div>
      </div>
    );
  }

  return (
    <div className="mx-auto flex w-full max-w-[980px] flex-col gap-8">
      {selectedConversation.turns.map((turn) => {
        const referenceLightboxImages = turn.referenceImages.map((image, index) => ({
          id: `${turn.id}-reference-${index}`,
          src: image.dataUrl,
        }));
        const successfulTurnImages = turn.images.flatMap((image) =>
          image.status === "success" && (image.b64_json || image.url)
            ? [
                {
                  id: image.id,
                  src: image.b64_json ? `data:image/png;base64,${image.b64_json}` : image.url || "",
                  sizeLabel: image.b64_json ? formatBase64ImageSize(image.b64_json) : undefined,
                  dimensions: imageDimensions[image.id],
                },
              ]
            : [],
        );

        return (
          <div key={turn.id} className="flex flex-col gap-4">
            <div className="flex justify-end">
              <div className="max-w-[88%] rounded-[28px] bg-stone-950 px-5 py-4 text-white shadow-[0_18px_50px_-32px_rgba(28,25,23,0.75)]">
                <div className="mb-2 flex flex-wrap justify-end gap-2 text-[11px] text-white/55">
                  <span>{turn.mode === "edit" ? "图生图" : "文生图"}</span>
                  <span>{getTurnStatusLabel(turn.status)}</span>
                  <span>{formatConversationTime(turn.createdAt)}</span>
                </div>
                <div className="whitespace-pre-wrap text-right text-[15px] leading-7">{turn.prompt}</div>
              </div>
            </div>

            <div className="flex justify-start">
              <div className="w-full">
                {turn.referenceImages.length > 0 ? (
                  <div className="mb-4 flex flex-col items-start gap-3 rounded-[28px] border border-stone-200 bg-white/80 p-4">
                    <div className="text-xs font-medium text-stone-500">参考图</div>
                    <div className="flex flex-wrap gap-3">
                      {turn.referenceImages.map((image, index) => (
                        <div key={`${turn.id}-${image.name}-${index}`} className="flex flex-col gap-2">
                          <button
                            type="button"
                            onClick={() => onOpenLightbox(referenceLightboxImages, index)}
                            className="group relative size-24 overflow-hidden rounded-2xl border border-stone-200/80 bg-stone-100/60 text-left transition hover:border-stone-300"
                            aria-label={`预览参考图 ${image.name || index + 1}`}
                          >
                            <img
                              src={image.dataUrl}
                              alt={image.name || `参考图 ${index + 1}`}
                              className="absolute inset-0 h-full w-full object-cover transition duration-200 group-hover:scale-[1.02]"
                            />
                          </button>
                          <Button
                            variant="outline"
                            size="sm"
                            className="h-8 rounded-full border-stone-200 bg-white text-xs text-stone-700 hover:bg-stone-50"
                            onClick={() => onContinueEdit(selectedConversation.id, image)}
                          >
                            <Sparkles className="size-4" />
                            继续用
                          </Button>
                        </div>
                      ))}
                    </div>
                  </div>
                ) : null}

                <div className="mb-3 flex flex-wrap items-center gap-2 text-xs text-stone-500">
                  <span>{turn.count} 张</span>
                  <span>{turn.size || "默认比例"}</span>
                  {turn.status === "queued" ? (
                    <span className="rounded-full bg-amber-50 px-3 py-1 text-amber-700">排队中</span>
                  ) : null}
                </div>

                <div className="columns-1 gap-4 space-y-4 sm:columns-2 xl:columns-3">
                  {turn.images.map((image, index) => {
                    if (image.status === "success" && (image.b64_json || image.url)) {
                      const currentIndex = successfulTurnImages.findIndex((item) => item.id === image.id);
                      const sizeLabel = image.b64_json ? formatBase64ImageSize(image.b64_json) : "";
                      const dimensions = imageDimensions[image.id];
                      const imageMeta = [sizeLabel, dimensions].filter(Boolean).join(" · ");
                      const serverImageSrc = image.url || "";
                      const imageSrc = image.b64_json ? `data:image/png;base64,${image.b64_json}` : serverImageSrc;
                      const downloadSrc = image.b64_json ? imageSrc : image.url || imageSrc;

                      return (
                        <div
                          key={image.id}
                          className="break-inside-avoid overflow-hidden rounded-[28px] border border-stone-200 bg-white shadow-sm"
                        >
                          <button
                            type="button"
                            onClick={() => onOpenLightbox(successfulTurnImages, Math.max(0, currentIndex))}
                            className="group block w-full cursor-zoom-in"
                          >
                            <img
                              src={imageSrc}
                              alt={`Generated result ${index + 1}`}
                              className="block h-auto w-full transition duration-200 group-hover:brightness-90"
                              onLoad={(event) => {
                                updateImageDimensions(
                                  image.id,
                                  event.currentTarget.naturalWidth,
                                  event.currentTarget.naturalHeight,
                                );
                              }}
                            />
                          </button>
                          <div className="flex flex-wrap items-center justify-between gap-2 px-3 py-3">
                            <div className="min-w-0 text-xs text-stone-500">{imageMeta || `图片 ${index + 1}`}</div>
                            <div className="flex items-center gap-2">
                              <a
                                href={downloadSrc}
                                download={`genapi-image-${image.id}.png`}
                                className="inline-flex h-8 items-center gap-1.5 rounded-full border border-stone-200 bg-white px-3 text-xs font-medium text-stone-700 transition hover:bg-stone-50"
                              >
                                <Download className="size-3.5" />
                                下载
                              </a>
                              <Button
                                variant="outline"
                                size="sm"
                                className="h-8 rounded-full border-stone-200 bg-white text-xs text-stone-700 hover:bg-stone-50"
                                onClick={() => onContinueEdit(selectedConversation.id, image)}
                              >
                                <Sparkles className="size-4" />
                                继续编辑
                              </Button>
                            </div>
                          </div>
                        </div>
                      );
                    }

                    if (image.status === "error") {
                      return (
                        <div
                          key={image.id}
                          className={cn(
                            "break-inside-avoid overflow-hidden rounded-[28px] border border-rose-200 bg-rose-50",
                            getImageAspectClass(turn.size),
                          )}
                        >
                          <div className="flex h-full items-center justify-center px-6 py-8 text-center text-sm leading-6 text-rose-600">
                            {image.error || "生成失败"}
                          </div>
                        </div>
                      );
                    }

                    return (
                      <div
                        key={image.id}
                        className={cn(
                          "break-inside-avoid overflow-hidden rounded-[28px] border border-stone-200/80 bg-stone-100/80",
                          getImageAspectClass(turn.size),
                        )}
                      >
                        <div className="flex h-full flex-col items-center justify-center gap-3 px-6 py-8 text-center text-stone-500">
                          <div className="rounded-full bg-white p-3 shadow-sm">
                            {turn.status === "queued" ? (
                              <Clock3 className="size-5" />
                            ) : (
                              <LoaderCircle className="size-5 animate-spin" />
                            )}
                          </div>
                          <p className="text-sm">{turn.status === "queued" ? "等待生成..." : "正在生成..."}</p>
                        </div>
                      </div>
                    );
                  })}
                </div>

                {turn.status === "error" && turn.error ? (
                  <div className="mt-4 rounded-2xl bg-amber-50/90 px-4 py-3 text-sm leading-6 text-amber-700">
                    {turn.error}
                  </div>
                ) : null}
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );
}

const promptIdeas = [
  {
    title: "产品海报",
    prompt: "一瓶冷萃咖啡放在晨光里的木桌上，干净商业摄影，浅景深",
  },
  {
    title: "头像插画",
    prompt: "戴耳机的橘猫程序员头像，柔和光线，精致数字插画",
  },
  {
    title: "场景概念",
    prompt: "雨夜里的未来城市小巷，霓虹反光，电影感广角构图",
  },
];

function getTurnStatusLabel(status: ImageTurnStatus) {
  if (status === "queued") {
    return "排队中";
  }
  if (status === "generating") {
    return "处理中";
  }
  if (status === "success") {
    return "已完成";
  }
  return "失败";
}

function getImageAspectClass(size: string) {
  if (size === "1:1") {
    return "aspect-square";
  }
  if (size === "16:9") {
    return "aspect-video";
  }
  if (size === "9:16") {
    return "aspect-[9/16]";
  }
  if (size === "4:3") {
    return "aspect-[4/3]";
  }
  if (size === "3:4") {
    return "aspect-[3/4]";
  }
  return "aspect-square";
}

function formatBase64ImageSize(base64: string) {
  const normalized = base64.replace(/\s/g, "");
  const padding = normalized.endsWith("==") ? 2 : normalized.endsWith("=") ? 1 : 0;
  const bytes = Math.max(0, Math.floor((normalized.length * 3) / 4) - padding);

  if (bytes >= 1024 * 1024) {
    return `${(bytes / 1024 / 1024).toFixed(2)} MB`;
  }
  if (bytes >= 1024) {
    return `${(bytes / 1024).toFixed(1)} KB`;
  }
  return `${bytes} B`;
}

function formatImageDimensions(width: number, height: number) {
  return `${width} x ${height}`;
}
