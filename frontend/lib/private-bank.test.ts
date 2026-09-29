/**
 * 私有题库纯逻辑单测（FR-13）：查询参数、上传预检、结果条文案、编辑态互转。
 *
 * 页面渲染靠联调 smoke（同项目惯例：vitest 只覆盖 lib 纯逻辑）。
 */

import { describe, expect, it } from "vitest";

import {
  EMPTY_PRIVATE_FILTERS,
  MAX_UPLOAD_BYTES,
  fileRejectReason,
  itemsToLines,
  linesToItems,
  privateStatusLabel,
  toPrivateQuery,
  uploadSummary,
  withPrivateFilter,
} from "@/lib/private-bank";

describe("toPrivateQuery", () => {
  it("空值不发参数（后端缺省 = 不筛且含已归档）", () => {
    const query = toPrivateQuery(EMPTY_PRIVATE_FILTERS, 10);
    expect([...query.keys()]).toEqual(["page", "page_size"]);
    expect(query.get("page")).toBe("1");
  });

  it("有值时发对应参数", () => {
    const query = toPrivateQuery(
      { status: "draft", domain: "rag", difficulty: "L3", page: 2 },
      10,
    );
    expect(query.get("status")).toBe("draft");
    expect(query.get("domain")).toBe("rag");
    expect(query.get("difficulty")).toBe("L3");
    expect(query.get("page")).toBe("2");
  });
});

describe("withPrivateFilter", () => {
  it("改筛选回第一页（否则停在空页上）", () => {
    const next = withPrivateFilter({ ...EMPTY_PRIVATE_FILTERS, page: 3 }, { status: "enabled" });
    expect(next.page).toBe(1);
    expect(next.status).toBe("enabled");
  });

  it("其余维度保持不动", () => {
    const next = withPrivateFilter(
      { status: "draft", domain: "rag", difficulty: "L2", page: 1 },
      { domain: "memory" },
    );
    expect(next).toEqual({ status: "draft", domain: "memory", difficulty: "L2", page: 1 });
  });
});

describe("privateStatusLabel", () => {
  it("复用的枚举在私有题里读作使用中/已归档", () => {
    expect(privateStatusLabel("enabled")).toBe("使用中");
    expect(privateStatusLabel("draft")).toBe("已归档");
  });

  it("未知状态原样显示（不猜）", () => {
    expect(privateStatusLabel("weird")).toBe("weird");
  });
});

describe("fileRejectReason", () => {
  it("接受 md/txt/pdf（含 .markdown）", () => {
    for (const name of ["a.md", "a.markdown", "a.txt", "a.pdf", "A.PDF"]) {
      expect(fileRejectReason({ name, size: 1024 })).toBeNull();
    }
  });

  it("后缀不支持时给出与后端一致的文案", () => {
    expect(fileRejectReason({ name: "笔记.docx", size: 1024 })).toBe("仅支持 .md / .txt / .pdf 文件");
  });

  it("超 2MB 拦下（不白传一趟）", () => {
    expect(fileRejectReason({ name: "a.md", size: MAX_UPLOAD_BYTES + 1 })).toBe(
      "文件过大（上限 2MB）",
    );
    expect(fileRejectReason({ name: "a.md", size: MAX_UPLOAD_BYTES })).toBeNull();
  });

  it("后缀优先于大小（两种都错时报更根本的那个）", () => {
    expect(fileRejectReason({ name: "a.zip", size: MAX_UPLOAD_BYTES + 1 })).toBe(
      "仅支持 .md / .txt / .pdf 文件",
    );
  });
});

describe("uploadSummary", () => {
  it("三份明细合成一句", () => {
    const result = uploadSummary({
      parsed: 5,
      imported: 3,
      duplicated: ["题一", "题二"],
      errors: [],
    });
    expect(result.tone).toBe("ok");
    expect(result.text).toBe("导入 3 题 · 重复 2 题（已存在，未覆盖）");
  });

  it("只有失败时不谎报成功", () => {
    const result = uploadSummary({
      parsed: 1,
      imported: 0,
      duplicated: [],
      errors: [{ question: "坏题", reason: "缺少答案" }],
    });
    expect(result.tone).toBe("warn");
    expect(result.text).toBe("失败 1 题");
  });

  it("没有解析到题目单独成一档（不是失败，错误列表也是空的）", () => {
    const result = uploadSummary({ parsed: 0, imported: 0, duplicated: [], errors: [] });
    expect(result.tone).toBe("none");
    expect(result.text).toContain("没有解析到题目");
  });

  it("全重复时不算成功（无新题入库）", () => {
    const result = uploadSummary({ parsed: 2, imported: 0, duplicated: ["一", "二"], errors: [] });
    expect(result.tone).toBe("warn");
    expect(result.text).toBe("重复 2 题（已存在，未覆盖）");
  });
});

describe("关键点与追问的编辑态互转", () => {
  it("一行一条；分号也算分隔（与后端解析同口径）", () => {
    expect(linesToItems("RDB 快照\nAOF 日志")).toEqual(["RDB 快照", "AOF 日志"]);
    expect(linesToItems("RDB 快照；AOF 日志")).toEqual(["RDB 快照", "AOF 日志"]);
  });

  it("空行与首尾空白丢掉（不产生空条目）", () => {
    expect(linesToItems("  a \n\n  \n b  ")).toEqual(["a", "b"]);
  });

  it("往返不增不减", () => {
    const items = ["RDB 快照", "AOF 日志"];
    expect(linesToItems(itemsToLines(items))).toEqual(items);
  });
});
