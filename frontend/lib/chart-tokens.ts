"use client";

import { useEffect, useState } from "react";

/**
 * 图表取色：recharts 把颜色写进 SVG 属性，属性值不解析 var()，
 * 因此从 :root 计算样式读出真实色值；系统明暗切换时重读。
 *
 * SSR/首帧用兜底常量（此刻读不到 document），挂载后立即换为真实令牌。
 */

export type ChartTokens = {
  primary: string;
  warning: string;
  border: string;
  muted: string;
  foreground: string;
  surface: string;
};

/** 兜底常量必须与 globals.css 的明色令牌一致（改令牌时同步这里）。 */
const FALLBACK: ChartTokens = {
  primary: "#2a49d8",
  warning: "#a5611b",
  border: "#d8d5cc",
  muted: "#64635e",
  foreground: "#16181c",
  surface: "#faf9f5",
};

function readTokens(): ChartTokens {
  if (typeof window === "undefined") return FALLBACK;
  const style = getComputedStyle(document.documentElement);
  const read = (name: keyof ChartTokens, cssVar: string) =>
    style.getPropertyValue(cssVar).trim() || FALLBACK[name];
  return {
    primary: read("primary", "--primary"),
    warning: read("warning", "--warning"),
    border: read("border", "--border"),
    muted: read("muted", "--muted-foreground"),
    foreground: read("foreground", "--foreground"),
    surface: read("surface", "--card"),
  };
}

export function useChartTokens(): { tokens: ChartTokens; mounted: boolean } {
  const [tokens, setTokens] = useState<ChartTokens>(FALLBACK);
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setTokens(readTokens());
    setMounted(true);
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    const update = () => setTokens(readTokens());
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, []);

  return { tokens, mounted };
}
