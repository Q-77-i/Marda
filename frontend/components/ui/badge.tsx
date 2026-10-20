import { mergeProps } from "@base-ui/react/merge-props";
import { useRender } from "@base-ui/react/use-render";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "cn";

/**
 * 标签：4px 圆角、等宽字、21px 高。
 * 默认是软钴蓝（当前状态），secondary 是纸灰（已完成），outline 是描边（类型/难度）。
 */
const badgeVariants = cva(
  "group/badge inline-flex h-[21px] w-fit shrink-0 items-center justify-center gap-1 overflow-hidden rounded-[4px] border px-1.5 font-mono text-[9.5px] leading-none font-medium whitespace-nowrap transition-colors focus-visible:border-ring focus-visible:ring-2 focus-visible:ring-ring/25 [&>svg]:pointer-events-none [&>svg]:size-3!",
  {
    variants: {
      variant: {
        default: "border-transparent bg-accent text-accent-foreground",
        secondary:
          "border-border bg-secondary text-secondary-foreground",
        destructive:
          "border-transparent bg-destructive/10 text-destructive focus-visible:ring-destructive/15",
        outline: "border-border bg-transparent text-muted-foreground",
        ghost: "border-transparent text-muted-foreground hover:bg-muted",
        warning: "border-transparent bg-warning/10 text-warning",
        success: "border-transparent bg-success/10 text-success",
        link: "border-transparent text-primary underline-offset-4 hover:underline",
      },
    },
    defaultVariants: {
      variant: "default",
    },
  },
);

function Badge({
  className,
  variant = "default",
  render,
  ...props
}: useRender.ComponentProps<"span"> & VariantProps<typeof badgeVariants>) {
  return useRender({
    defaultTagName: "span",
    props: mergeProps<"span">(
      {
        className: cn(badgeVariants({ variant }), className),
      },
      props,
    ),
    render,
    state: {
      slot: "badge",
      variant,
    },
  });
}

export { Badge, badgeVariants };
