import type { ReactNode } from "react";

import { AppHeader } from "@/components/app-header";
import { UserMenu } from "@/components/user-menu";

/** 页面宽度两档：工具/档案类多栏页用 default，单列阅读页（决策回放）用 narrow。 */
const WIDTHS = {
  default: "max-w-5xl",
  narrow: "max-w-3xl",
} as const;

/**
 * 页面外壳：页头 + 主内容容器。各页共用一份——容器宽度、水平留白、垂直留白
 * 与顶栏内沿对齐，避免每个页面各自决定一次（此前同一页的加载/错误/正常三态
 * 用了三种 main，切态时页面宽度会跳）。
 *
 * `nav` 默认渲染主导航；报告页/回放页是沉浸式，传 false。
 * `right` 默认给用户菜单；报告/回放页换成页内动作。
 */
export function PageShell({
  children,
  right,
  nav = true,
  width = "default",
}: {
  children: ReactNode;
  right?: ReactNode;
  nav?: boolean;
  width?: keyof typeof WIDTHS;
}) {
  return (
    <div className="min-h-[100dvh]">
      <AppHeader nav={nav} width={width} right={right === undefined ? <UserMenu /> : right} />
      <main
        className={`mx-auto flex w-full flex-col gap-6 px-4 py-8 sm:px-6 sm:py-10 ${WIDTHS[width]}`}
      >
        {children}
      </main>
    </div>
  );
}
