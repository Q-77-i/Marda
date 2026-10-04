/**
 * 图片通道纯逻辑（P2-M6 FR-26）：选择的校验 / 压缩目标尺寸 / 附件提示。
 *
 * 浏览器专属的部分（canvas 压缩、上传）在 `lib/image-client.ts`——那里进不了 vitest；
 * 这里只放可测的判据，参数与后端 `backend/app/tools/images.py` 对齐。
 *
 * 尺寸口径来自探针实测（2026-10-04）：Retina 2800px 的代码截图降到长边 1568 仍能读对
 * 函数名（~560 token），降到 800 就掉了可读性——1568 是「读得清 + token 低」的折中。
 */

export const MAX_IMAGES_PER_MESSAGE = 3; // 与后端 images.MAX_IMAGES_PER_MESSAGE 同值
export const MAX_SOURCE_BYTES = 10 * 1024 * 1024; // 选择上限（压缩前）；后端兜底 8MB
export const TARGET_LONG_EDGE = 1568;
export const ACCEPTED_TYPES = ["image/png", "image/jpeg", "image/webp"] as const;
export const IMAGE_ACCEPT = ACCEPTED_TYPES.join(",");

/**
 * 附件说明（2026-10-04 用户补充口径）：**如实交代图用在哪**——题库题出题暂不结合截图
 * （题面来自题库），不写清会让用户以为「传了白传」。
 */
export const ATTACH_HINT = "截图会随回答用于评分与追问；项目深挖环节出题也会结合截图。";

export const ATTACH_TITLE = "附上代码截图或架构图（可多选，发送前可移除）";

export type PickedFile = { type: string; size: number };

/** 单张选择的校验：返回错误文案或 null（数量上限按「已附 + 本次新增」计）。 */
export function pickError(file: PickedFile, existing: number): string | null {
  if (existing >= MAX_IMAGES_PER_MESSAGE) return `最多附 ${MAX_IMAGES_PER_MESSAGE} 张图`;
  if (!(ACCEPTED_TYPES as readonly string[]).includes(file.type)) {
    return "仅支持 PNG / JPEG / WebP 图片";
  }
  if (file.size > MAX_SOURCE_BYTES) {
    return `图片超过 ${MAX_SOURCE_BYTES / 1024 / 1024}MB，请先裁剪`;
  }
  return null;
}

/** 压缩目标尺寸：长边不超过 longEdge 等比缩小；本来就小则原样（不放大）。 */
export function targetSize(
  width: number,
  height: number,
  longEdge: number = TARGET_LONG_EDGE,
): { width: number; height: number } {
  const scale = Math.min(1, longEdge / Math.max(width, height));
  return {
    width: Math.max(1, Math.round(width * scale)),
    height: Math.max(1, Math.round(height * scale)),
  };
}
