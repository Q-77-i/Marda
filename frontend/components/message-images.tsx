"use client";

import { useEffect, useState } from "react";

import { authorizedFetch } from "@/lib/http";

/**
 * 消息里的图片缩略图（P2-M6 FR-26）：面试气泡与只读回放共用同一条渲染路径。
 *
 * 图走鉴权端点（`GET /api/interviews/{id}/images/{image_id}`），不能直接 <img src>——
 * 浏览器图片标签带不了 Authorization 头，必须 fetch → objectURL。
 * objectURL 按「场次/id」缓存：同一条消息反复渲染只取一次；失败给占位不静默。
 */

const cache = new Map<string, string>();

function useImageUrl(interviewId: string, imageId: string): string | "error" | null {
  const key = `${interviewId}/${imageId}`;
  const [url, setUrl] = useState<string | "error" | null>(() => cache.get(key) ?? null);

  useEffect(() => {
    const cached = cache.get(key);
    if (cached) {
      setUrl(cached);
      return;
    }
    let active = true;
    authorizedFetch(`/api/interviews/${interviewId}/images/${imageId}`)
      .then(async (response) => {
        if (!response.ok) throw new Error();
        return response.blob();
      })
      .then((blob) => {
        const objectUrl = URL.createObjectURL(blob);
        cache.set(key, objectUrl);
        if (active) setUrl(objectUrl);
      })
      .catch(() => {
        if (active) setUrl("error");
      });
    return () => {
      active = false;
    };
  }, [key, interviewId, imageId]);

  return url;
}

function Thumbnail({
  interviewId,
  imageId,
  onOpen,
}: {
  interviewId: string;
  imageId: string;
  onOpen: (url: string) => void;
}) {
  const url = useImageUrl(interviewId, imageId);

  if (url === null) {
    return <div className="h-20 w-28 animate-pulse rounded-lg bg-foreground/10" aria-hidden />;
  }
  if (url === "error") {
    return (
      <div className="flex h-20 w-28 items-center justify-center rounded-lg bg-foreground/10 px-2 text-center text-[10px] text-muted-foreground">
        图片加载失败
      </div>
    );
  }
  return (
    <button
      type="button"
      onClick={() => onOpen(url)}
      className="overflow-hidden rounded-lg ring-1 ring-foreground/15 transition-opacity hover:opacity-90"
      title="点击查看大图"
    >
      {/* eslint-disable-next-line @next/next/no-img-element -- 鉴权 blob 图，next/image 不适用 */}
      <img src={url} alt="候选人上传的截图" className="max-h-32 w-auto" />
    </button>
  );
}

/** 一条消息的图片区；点击缩略图进全屏查看（再点任意处关闭）。 */
export function MessageImages({
  interviewId,
  imageIds,
}: {
  interviewId: string;
  imageIds: string[];
}) {
  const [zoomed, setZoomed] = useState<string | null>(null);

  if (imageIds.length === 0) return null;
  return (
    <div className="mb-1.5 flex flex-wrap gap-1.5">
      {imageIds.map((imageId) => (
        <Thumbnail key={imageId} interviewId={interviewId} imageId={imageId} onOpen={setZoomed} />
      ))}
      {zoomed && (
        <div
          role="presentation"
          onClick={() => setZoomed(null)}
          className="fixed inset-0 z-50 flex items-center justify-center bg-background/80 p-6 backdrop-blur-sm"
        >
          {/* eslint-disable-next-line @next/next/no-img-element -- 同上 */}
          <img
            src={zoomed}
            alt="候选人上传的截图（大图）"
            className="max-h-[90dvh] max-w-full rounded-lg shadow-lg"
          />
        </div>
      )}
    </div>
  );
}
