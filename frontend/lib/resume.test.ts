import { describe, expect, it } from "vitest";

import { RESUME_DISCLOSURE, resumeDigest, resumeSource } from "@/lib/resume";

describe("resumeSource（解析用哪一份）", () => {
  it("有文件就用文件", () => {
    expect(resumeSource({ name: "简历.pdf" }, "")).toBe("file");
  });

  it("没文件但有粘贴文本就用文本", () => {
    expect(resumeSource(null, "我是张三……")).toBe("text");
  });

  it("文件优先于文本（选了文件说明意图明确）", () => {
    expect(resumeSource({ name: "简历.md" }, "同时粘了文本")).toBe("file");
  });

  it("都没有 → null（解析按钮据此禁用）", () => {
    expect(resumeSource(null, "")).toBeNull();
    expect(resumeSource(null, "   \n  ")).toBeNull(); // 纯空白不算输入
  });
});

describe("resumeDigest（解析结果摘要，不含原文）", () => {
  it("项目 / 技能 / 字数三段", () => {
    expect(resumeDigest({ projects: ["A", "B"], skills: ["Python", "RAG"], chars: 800 })).toBe(
      "已读到 2 段项目经历 · 2 项技能 · 约 800 字",
    );
  });

  it("技能为空时只给两段（不写「0 项技能」）", () => {
    expect(resumeDigest({ projects: ["A"], skills: [], chars: 120 })).toBe(
      "已读到 1 段项目经历 · 约 120 字",
    );
  });

  it("读到 0 段项目经历照实说——解析错要当场看得见", () => {
    expect(resumeDigest({ projects: [], skills: [], chars: 30 })).toBe(
      "已读到 0 段项目经历 · 约 30 字",
    );
  });
});

describe("如实交代", () => {
  it("写明会发给模型、用途、以及可以不用", () => {
    expect(RESUME_DISCLOSURE).toContain("发送给模型");
    expect(RESUME_DISCLOSURE).toContain("不解析也可以");
  });
});
