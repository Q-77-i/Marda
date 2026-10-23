import { describe, expect, it } from "vitest";

import { totalPages } from "@/lib/pagination";

describe("totalPages", () => {
  it("按每页条数向上取整且至少 1 页", () => {
    expect(totalPages(0, 10)).toBe(1);
    expect(totalPages(null, 10)).toBe(1);
    expect(totalPages(21, 10)).toBe(3);
  });
});
