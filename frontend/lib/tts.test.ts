import { describe, expect, it } from "vitest";

import { chooseChineseVoice } from "@/lib/tts";

describe("浏览器语音降级", () => {
  it("优先 zh-CN", () => {
    const voices = [
      { lang: "en-US", name: "A" },
      { lang: "zh-TW", name: "B" },
      { lang: "zh-CN", name: "C" },
    ];
    expect(chooseChineseVoice(voices)?.name).toBe("C");
  });

  it("没有 zh-CN 时退回任意中文（兼容 zh_CN 写法）", () => {
    expect(chooseChineseVoice([{ lang: "en-US" }, { lang: "zh_TW" }])?.lang).toBe(
      "zh_TW",
    );
  });

  it("没有中文音色返回 null（交给系统默认音色）", () => {
    expect(chooseChineseVoice([{ lang: "en-US" }])).toBeNull();
    expect(chooseChineseVoice([])).toBeNull();
  });
});
