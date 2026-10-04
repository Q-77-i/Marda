/**
 * 图片通道的浏览器侧（P2-M6 FR-26）：canvas 压缩 + 上传。
 *
 * 浏览器专属、不进 vitest（同 lib/asr-client.ts 的分工）；可测的判据全在 lib/vision.ts。
 * 压缩在**发送前**做（选择时就压好，预览即上传物）——上传体积小、DeepSeek 按像素计费。
 */

import { authorizedFetch, responseError } from "@/lib/http";
import { TARGET_LONG_EDGE, targetSize } from "@/lib/vision";

/** 压缩到长边 ≤TARGET_LONG_EDGE 的 JPEG；取不到画布/编码失败时原样返回（后端还有兜底校验）。 */
export async function compressImage(file: File): Promise<Blob> {
  const bitmap = await createImageBitmap(file);
  const { width, height } = targetSize(bitmap.width, bitmap.height, TARGET_LONG_EDGE);
  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  const ctx = canvas.getContext("2d");
  if (!ctx) {
    bitmap.close();
    return file;
  }
  ctx.drawImage(bitmap, 0, 0, width, height);
  bitmap.close();
  const blob = await new Promise<Blob | null>((resolve) =>
    canvas.toBlob(resolve, "image/jpeg", 0.88),
  );
  return blob ?? file;
}

/** 上传一张图，返回 image_id（两段式：先传图拿 id，再随消息发 id 列表）。 */
export async function uploadImage(interviewId: string, blob: Blob): Promise<string> {
  const form = new FormData();
  form.append("file", blob, "screenshot.jpg");
  const response = await authorizedFetch(`/api/interviews/${interviewId}/images`, {
    method: "POST",
    body: form,
  });
  if (!response.ok) throw new Error(await responseError(response));
  const payload = (await response.json()) as { image_id: string };
  return payload.image_id;
}
