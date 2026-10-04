/**
 * 摄像头通道的浏览器 API 层（P2-M7 FR-27）：只做一件事——取流。
 *
 * 画面消费（`video.srcObject`）在 `components/interview-room.tsx`，
 * 收摊（停轨道）在 `lib/camera.ts` 的 `stopStream`——**本文件没有 canvas、没有抓帧、
 * 没有任何把画面发出去的路径**（FR-27：帧不上传、不落库、AI 不看）。
 * 失败原样抛，文案分流在 `cameraErrorMessage`。
 */

import { CAMERA_CONSTRAINTS } from "@/lib/camera";

/** 取一路本机画面（audio 显式关闭，见 CAMERA_CONSTRAINTS）。 */
export async function openCamera(): Promise<MediaStream> {
  return navigator.mediaDevices.getUserMedia(CAMERA_CONSTRAINTS);
}
