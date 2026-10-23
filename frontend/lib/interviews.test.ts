import { describe, expect, it } from "vitest";

import {
  EMPTY_INTERVIEW_FILTERS,
  pageAfterDelete,
  sinceIso,
  toInterviewQuery,
  withInterviewFilter,
} from "@/lib/interviews";

describe("withInterviewFilter", () => {
  it("改任何过滤条件都回到第一页", () => {
    const next = withInterviewFilter(
      { ...EMPTY_INTERVIEW_FILTERS, page: 3 },
      { status: "running" },
    );
    expect(next).toEqual({ ...EMPTY_INTERVIEW_FILTERS, status: "running", page: 1 });
  });

  it("翻页不动过滤条件", () => {
    const next = withInterviewFilter(
      { status: "finished", type: "behavioral", since: "30d", page: 1 },
      { page: 2 },
    );
    expect(next).toEqual({
      status: "finished",
      type: "behavioral",
      since: "30d",
      page: 2,
    });
  });
});

describe("sinceIso", () => {
  const now = new Date("2026-10-23T09:00:00Z");

  it("全部不发 since", () => {
    expect(sinceIso("", now)).toBeUndefined();
  });

  it("近 7 天 = UTC 日期减 7 天", () => {
    expect(sinceIso("7d", now)).toBe("2026-10-16");
  });

  it("近 30 天", () => {
    expect(sinceIso("30d", now)).toBe("2026-09-23");
  });
});

describe("toInterviewQuery", () => {
  const now = new Date("2026-10-23T00:00:00Z");

  it("空过滤只发分页参数", () => {
    expect(Object.fromEntries(toInterviewQuery(EMPTY_INTERVIEW_FILTERS, 10, now))).toEqual({
      page: "1",
      page_size: "10",
    });
  });

  it("过滤值转查询参数，since 由区间算出", () => {
    const query = toInterviewQuery(
      { status: "running", type: "tech", since: "7d", page: 2 },
      5,
      now,
    );
    expect(Object.fromEntries(query)).toEqual({
      status: "running",
      type: "tech",
      since: "2026-10-16",
      page: "2",
      page_size: "5",
    });
  });
});

describe("pageAfterDelete", () => {
  it("删到空页且不在第一页 → 回退一页", () => {
    expect(pageAfterDelete(3, 0)).toBe(2);
  });

  it("第一页删空不回退", () => {
    expect(pageAfterDelete(1, 0)).toBe(1);
  });

  it("本页还有剩余 → 不动", () => {
    expect(pageAfterDelete(2, 3)).toBe(2);
  });
});
