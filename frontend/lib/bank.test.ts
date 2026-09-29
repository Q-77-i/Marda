/**
 * 题库纯逻辑单测（FR-12 / FR-14）：查询参数构建、分页、容量禁用判据。
 *
 * 页面渲染靠联调 smoke（同项目惯例：vitest 只覆盖 lib 纯逻辑）。
 */

import { describe, expect, it } from "vitest";

import type { CapacityOption } from "@/lib/api";
import {
  EMPTY_FILTERS,
  capacityFor,
  selectOptions,
  shortfallMessage,
  toQuery,
  totalPages,
  withFilter,
} from "@/lib/bank";

const LABELS: Record<string, string> = {
  "planning-reasoning": "规划与推理范式",
  rag: "RAG",
};

function option(overrides: Partial<CapacityOption> = {}): CapacityOption {
  return {
    difficulty: "L3",
    base: "L3",
    question_count: 15,
    ok: true,
    shortfalls: [],
    ...overrides,
  };
}

describe("toQuery", () => {
  it("空值不发参数（后端把缺省视为不筛）", () => {
    const query = toQuery(EMPTY_FILTERS, 10);
    expect([...query.keys()]).toEqual(["page", "page_size"]);
    expect(query.get("page")).toBe("1");
    expect(query.get("page_size")).toBe("10");
  });

  it("关键词去空格后作为 q 发出", () => {
    const query = toQuery({ ...EMPTY_FILTERS, q: "  RAG 切片  " }, 10);
    expect(query.get("q")).toBe("RAG 切片");
  });

  it("四个筛选维度齐发", () => {
    const query = toQuery(
      { domain: "rag", difficulty: "L3", company: "腾讯", round: "一面", q: "", page: 2 },
      10,
    );
    expect(query.get("domain")).toBe("rag");
    expect(query.get("difficulty")).toBe("L3");
    expect(query.get("company")).toBe("腾讯");
    expect(query.get("round")).toBe("一面");
    expect(query.get("page")).toBe("2");
  });
});

describe("withFilter", () => {
  it("改任何筛选都回到第一页", () => {
    const filters = withFilter({ ...EMPTY_FILTERS, page: 3 }, { domain: "rag" });
    expect(filters).toEqual({ ...EMPTY_FILTERS, domain: "rag", page: 1 });
  });
});

describe("totalPages", () => {
  it("按每页条数向上取整且至少 1 页", () => {
    expect(totalPages(0, 10)).toBe(1);
    expect(totalPages(null, 10)).toBe(1);
    expect(totalPages(21, 10)).toBe(3);
  });
});

describe("capacityFor / shortfallMessage", () => {
  it("容量未加载完不禁用（不因网络慢挡住用户）", () => {
    expect(capacityFor(null, "L3", 15)).toBeNull();
    expect(shortfallMessage(null, (d) => LABELS[d] ?? d)).toBeNull();
  });

  it("按难度 + 题数取结论", () => {
    const options = [option({ question_count: 10 }), option({ question_count: 15, ok: false })];
    expect(capacityFor(options, "L3", 15)?.ok).toBe(false);
    expect(capacityFor(options, "L3", 5)).toBeNull();
  });

  it("不足提示说明缺在哪个域、差几题", () => {
    const message = shortfallMessage(
      option({
        ok: false,
        shortfalls: [
          { domain: "planning-reasoning", required: 2, available: 1 },
          { domain: "rag", required: 3, available: 2 },
        ],
      }),
      (d) => LABELS[d] ?? d,
    );
    expect(message).toBe("题库直供不足：规划与推理范式（需 2 题，题库 1 题）、RAG（需 3 题，题库 2 题）");
  });

  it("可直供时无提示", () => {
    expect(shortfallMessage(option(), (d) => d)).toBeNull();
  });
});

describe("selectOptions", () => {
  it("首项是全部，其余只给值（计数不展示，结果区给总数）", () => {
    expect(selectOptions(undefined)).toEqual([{ value: "", label: "全部" }]);
    expect(selectOptions([{ value: "腾讯", count: 62 }])).toEqual([
      { value: "", label: "全部" },
      { value: "腾讯", label: "腾讯" },
    ]);
  });
});
