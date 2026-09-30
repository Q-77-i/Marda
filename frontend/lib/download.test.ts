import { describe, expect, it } from "vitest";

import { reportFileName } from "@/lib/download";

describe("reportFileName", () => {
  it("常规岗位名直接进文件名", () => {
    expect(reportFileName("Agent/AI 工程师", "a1b2c3d4e5")).toBe(
      "码达能力评估报告-Agent-AI 工程师.pdf",
    );
    expect(reportFileName("后端开发", "a1b2c3d4e5")).toBe("码达能力评估报告-后端开发.pdf");
  });

  it("岗位名里的非法字符换成连字符（不能让斜杠穿进文件名当路径）", () => {
    expect(reportFileName('RAG: 检索/生成 "专家"', "id")).toBe(
      "码达能力评估报告-RAG- 检索-生成 -专家-.pdf",
    );
  });

  it("岗位名空、只有空白或只有标点时退回场次号", () => {
    expect(reportFileName("", "a1b2c3d4e5")).toBe("码达能力评估报告-a1b2c3d4.pdf");
    expect(reportFileName("   ", "a1b2c3d4e5")).toBe("码达能力评估报告-a1b2c3d4.pdf");
    expect(reportFileName("///", "a1b2c3d4e5")).toBe("码达能力评估报告-a1b2c3d4.pdf");
  });

  it("超长岗位名截断到 40 字符（文件名上限按字节算，中文一字 3 字节）", () => {
    const name = reportFileName("长".repeat(60), "id");
    expect(name).toBe(`码达能力评估报告-${"长".repeat(40)}.pdf`);
  });
});
