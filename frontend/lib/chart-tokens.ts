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

const FALLBACK: ChartTokens = {
  primary: "#056a9d",
  warning: "#ba7917",
  border: "#e2e4e7",
  muted: "#666d74",
  foreground: "#1f2429",
  surface: "#ffffff",
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
