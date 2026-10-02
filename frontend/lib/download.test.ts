import { describe, expect, it } from "vitest";

import { reportFileName } from "@/lib/download";

/** 2026-10-01 15:54 UTC+8（ISO 串本身是 UTC）。 */
const CREATED = "2026-10-01T07:54:00+00:00";

function name(
  position: string,
  interviewId = "a1b2c3d4e5",
  questionCount = 10,
  createdAt = CREATED,
): string {
  return reportFileName({ position, interviewId, questionCount, createdAt });
}

describe("reportFileName", () => {
  it("常规岗位名直接进文件名，尾部带题量与该场时间", () => {
    expect(name("Agent/AI 工程师", "a1b2c3d4e5", 15)).toBe(
      "码达-能力评估-Agent-AI 工程师-15题-20261001-1554.pdf",
    );
    expect(name("后端开发", "a1b2c3d4e5", 5)).toBe("码达-能力评估-后端开发-5题-20261001-1554.pdf");
  });

  it("岗位名里的非法字符换成连字符（不能让斜杠穿进文件名当路径）", () => {
    expect(name('RAG: 检索/生成 "专家"')).toBe(
      "码达-能力评估-RAG- 检索-生成 -专家--10题-20261001-1554.pdf",
    );
  });

  it("岗位名空、只有空白或只有标点时退回场次号", () => {
    expect(name("")).toBe("码达-能力评估-a1b2c3d4-10题-20261001-1554.pdf");
    expect(name("   ")).toBe("码达-能力评估-a1b2c3d4-10题-20261001-1554.pdf");
    expect(name("///")).toBe("码达-能力评估-a1b2c3d4-10题-20261001-1554.pdf");
  });

  it("超长岗位名截断到 40 字符（文件名上限按字节算，中文一字 3 字节）", () => {
    expect(name("长".repeat(60))).toBe(
      `码达-能力评估-${"长".repeat(40)}-10题-20261001-1554.pdf`,
    );
  });

  it("时间解析不了就省掉时间戳，文件名照样能落盘", () => {
    expect(name("后端开发", "a1b2c3d4e5", 10, "不是时间")).toBe(
      "码达-能力评估-后端开发-10题.pdf",
    );
  });

  it("同一场重复下载名字相同，不同场次名字不同（不再靠浏览器加 -2 区分）", () => {
    const first = name("后端开发", "aaa", 10, "2026-10-01T07:54:00+00:00");
    const same = name("后端开发", "aaa", 10, "2026-10-01T07:54:00+00:00");
    const other = name("后端开发", "bbb", 10, "2026-10-01T07:55:00+00:00");
    expect(first).toBe(same);
    expect(first).not.toBe(other);
  });
});
