import { describe, expect, it } from "vitest";

import {
  TTS_VOICE_KEY,
  chooseChineseVoice,
  parseSavedVoice,
  takeFirstSentence,
} from "@/lib/tts";

describe("首句切分（TTS 提前合成）", () => {
  it("在标点处切出首句，保留剩余", () => {
    expect(
      takeFirstSentence("你提到的对抗性用例很扎实，但我想听听另一面：如果重做"),
    ).toEqual({
      sentence: "你提到的对抗性用例很扎实，但我想听听另一面：",
      rest: "如果重做",
    });
  });

  it("短句并到下一句，避免为碎片单独请求 TTS", () => {
    const result = takeFirstSentence("好。这一句足够长。");
    expect(result?.sentence).toBe("好。这一句足够长。");
  });

  it("没有完整句子返回 null（继续攒）", () => {
    expect(takeFirstSentence("还没有说完")).toBeNull();
    expect(takeFirstSentence("好。")).toBeNull();
  });
});

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

  it("避开搞怪音色，优先自然的中文音色", () => {
    const voices = [
      { lang: "zh-CN", name: "Grandma" },
      { lang: "zh-CN", name: "Tingting" },
      { lang: "zh-CN", name: "Eddy" },
    ];
    expect(chooseChineseVoice(voices)?.name).toBe("Tingting");
  });

  it("Enhanced 变体优先于普通变体", () => {
    const voices = [
      { lang: "zh-CN", name: "Tingting" },
      { lang: "zh-CN", name: "Tingting (Enhanced)" },
    ];
    expect(chooseChineseVoice(voices)?.name).toBe("Tingting (Enhanced)");
  });

  it("没有 zh-CN 时，zh-TW/zh-HK 里也避开搞怪音色", () => {
    const voices = [
      { lang: "zh-TW", name: "Grandpa" },
      { lang: "zh-TW", name: "Meijia" },
    ];
    expect(chooseChineseVoice(voices)?.name).toBe("Meijia");
  });
});

describe("音色偏好持久化", () => {
  it("键名与空值归一", () => {
    expect(TTS_VOICE_KEY).toBe("marda.ttsVoice");
    expect(parseSavedVoice(null)).toBeNull();
    expect(parseSavedVoice("  ")).toBeNull();
    expect(parseSavedVoice("  Tingting  ")).toBe("Tingting");
  });
});
