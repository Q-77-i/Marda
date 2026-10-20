import type { ReactNode } from "react";

import { AppRail } from "@/components/app-rail";
import { cn } from "@/lib/utils";

/** 页面宽度两档：工具/档案类多栏页用 default，单列阅读页（决策回放）用 narrow。 */
const WIDTHS = {
  default: "max-w-[1440px]",
  narrow: "max-w-3xl",
} as const;

/**
 * 页面外壳（界面重塑）：左栏 + 主内容。
 *
 * 不再有顶栏；导航、用户与本地演示说明都在左栏。主内容用统一的水平留白，
 * PageHeader 通过相同的负边距把分隔线拉满整行。
 */
export function PageShell({
  children,
  width = "default",
}: {
  children: ReactNode;
  width?: keyof typeof WIDTHS;
}) {
  return (
    <div className="min-h-[100dvh] md:grid md:grid-cols-[74px_minmax(0,1fr)] xl:grid-cols-[236px_minmax(0,1fr)]">
      <AppRail />
      <main className="min-w-0">
        <div
          className={cn(
            "mx-auto flex w-full flex-col gap-5 px-5 py-5 md:px-8 md:py-6",
            WIDTHS[width],
          )}
        >
          {children}
        </div>
      </main>
    </div>
  );
}
