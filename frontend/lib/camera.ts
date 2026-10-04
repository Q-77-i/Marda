/**
 * 摄像头通道纯逻辑（P2-M7 FR-27）：开关判据、错误分流、轨道收摊、如实交代文案。
 *
 * FR-27 的红线是**帧不上传、不落库、AI 不看**——全仓关于画面只有
 * 「getUserMedia → video.srcObject」一条路：这里与 camera-client 里没有 canvas、
 * 没有抓帧、没有任何把画面发出去的函数（本文件 `stopStream` 是唯一的流操作）。
 *
 * 页面里的分支进不了 vitest（P2-M3 的教训），凡是能做成纯函数的一律放这里。
 */

/** 摄像头开关的持久化键（localStorage；不可用时静默当没开过，同语音模式）。 */
export const CAMERA_MODE_KEY = "marda.cameraMode";

/** 如实交代（M6 截图提示同款口径）：别让用户以为自己被拍下来传走了。 */
export const CAMERA_HINT = "画面仅在本机显示，不上传、不保存、面试官看不到";

/** 「我」tile 上的小字角标（空间有限，用短版；完整口径在开关按钮的 title 里）。 */
export const CAMERA_CAPTION = "仅本机可见，不上传";

/**
 * 取流约束：分辨率用 **ideal 不用 exact**（exact 会给不支持的设备抛 OverconstrainedError）；
 * `audio` 必须显式 false——只开画面，不顺手把麦克风也打开。
 */
export const CAMERA_CONSTRAINTS: MediaStreamConstraints = {
  video: { facingMode: "user", width: { ideal: 1280 }, height: { ideal: 720 } },
  audio: false,
};

/** 摄像头 UI 状态（组件据此渲染；纯函数便于钉死优先级）。 */
export type CameraUiState = {
  /** 画面开着（有流） */
  on: boolean;
  /** 正在取流 */
  starting: boolean;
  /** 浏览器支持（有 getUserMedia；且处于安全上下文） */
  supported: boolean;
};

/**
 * 开关是否禁用——**「开」与「关」都是同一个按钮**：开着的时候必须可点（否则关不掉），
 * 与 M5 录音键「disabled 写成 !canStart 的反」是同一类坑，单测钉死。
 */
export function cameraDisabled(state: CameraUiState): boolean {
  if (state.starting) return true;
  return !state.supported;
}

/** 取流失败的原因分流（降级链：明说原因 + 给出路，不弹死胡同）。 */
export function cameraErrorMessage(reason: unknown): string {
  const name = reason instanceof DOMException ? reason.name : "";
  if (name === "NotAllowedError" || name === "SecurityError") {
    return "未获得摄像头权限，画面未开启（可在浏览器地址栏重新允许）。";
  }
  if (name === "NotFoundError" || name === "OverconstrainedError") {
    return "没有找到可用的摄像头，画面未开启。";
  }
  if (name === "NotReadableError" || name === "AbortError") {
    return "摄像头被其他程序占用，暂时无法开启。";
  }
  if (reason instanceof DOMException) return `摄像头开启失败（${name}）。`;
  if (reason instanceof Error && reason.message) return reason.message;
  return "摄像头开启失败，请稍后重试。";
}

/**
 * 关掉一条流上的全部轨道——**三处收摊（关闭开关 / 离开页面 / 面试结束）的唯一出口**。
 * 漏掉任何一处都会让摄像头指示灯常亮，用户会以为被偷拍；收成一个函数就没有第二份实现可漂移。
 */
export function stopStream(stream: Pick<MediaStream, "getTracks"> | null): void {
  if (!stream) return;
  for (const track of stream.getTracks()) track.stop();
}
