import { cn } from "cn";

/**
 * 行内展开面板的材质：比卡片低一级——圆角小一档、用 border 而非 ring、底色下沉。
 * 单独导出供 `<form>` 这类非 div 元素复用同一规格（私有题库的行内编辑表单）。
 */
export const inlinePanelClass =
  "mt-3 flex flex-col gap-3 rounded-lg border bg-muted/30 p-3 " +
  "animate-in fade-in-0 slide-in-from-top-1 duration-200 ease-out";

/** 行内展开面板：列表项下方展开的明细块（题库答案、私有题编辑、推荐答案）。 */
function InlinePanel({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div data-slot="inline-panel" className={cn(inlinePanelClass, className)} {...props} />
  );
}

export { InlinePanel };
