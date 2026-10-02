/**
 * PCM 采集 worklet（P2-M5 FR-24）。
 *
 * 放在 public/ 而不是随包构建：AudioWorklet 要求 `addModule` 加载**独立文件**（浏览器不认
 * 打包后的模块图），故只能是纯 JS 静态资源。
 *
 * 职责只有一件：把输入缓冲攒到 ~2048 帧（约 43ms）再 postMessage——每个 render quantum
 * （128 帧）都发消息会把主线程打爆，而攒太久又增加端到端延迟。
 */
class AsrCaptureProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this._parts = [];
    this._frames = 0;
    this._target = 2048;
  }

  process(inputs) {
    const channel = inputs[0] && inputs[0][0];
    if (!channel) return true; // 通道暂时没数据（静音/未连接）：继续跑，别退出
    this._parts.push(new Float32Array(channel)); // 必须拷贝：输入缓冲会被复用
    this._frames += channel.length;
    if (this._frames >= this._target) {
      const merged = new Float32Array(this._frames);
      let offset = 0;
      for (const part of this._parts) {
        merged.set(part, offset);
        offset += part.length;
      }
      this._parts = [];
      this._frames = 0;
      this.port.postMessage(merged, [merged.buffer]); // 转移所有权，零拷贝
    }
    return true;
  }
}

registerProcessor("asr-capture", AsrCaptureProcessor);
