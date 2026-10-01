"use client";

import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { ChartFrame, tooltipStyles } from "@/components/report-charts";
import { useChartTokens } from "@/lib/chart-tokens";
import type { ProfileChartRow } from "@/lib/profile";

/**
 * 能力档案图表（FR-19）。
 *
 * 形态选择：总分用一张主曲线；五维与知识域用**小倍图**（每项一张迷你曲线）而不是多线图——
 * 5 条 / 6 条线缠在一张图里，两条线交叉的地方用户分不清谁是谁，还得配图例和一套分类色板；
 * 各自成图则「这条线在涨还是跌」一眼可见，每张只用一个主色。
 */

/** Y 轴固定 0–5：跨场次的图必须同刻度，否则「涨了」可能只是纵轴被放大了。 */
const Y_DOMAIN: [number, number] = [0, 5];
const Y_TICKS = [0, 1, 2, 3, 4, 5];

/** 总分曲线（主图）：点可点，跳去那一场的报告。 */
export function OverallTrend({
  rows,
  onSelect,
}: {
  rows: ProfileChartRow[];
  onSelect: (interviewId: string) => void;
}) {
  const { tokens, mounted } = useChartTokens();

  return (
    <ChartFrame height={260}>
      {mounted && (
        <ResponsiveContainer width="100%" height="100%">
          <LineChart
            data={rows}
            margin={{ top: 8, right: 16, bottom: 4, left: -16 }}
            onClick={(state) => {
              // recharts 3 的回调给的是**数据下标**而不是数据本身：按下标取行；
              // 取不到（点在图外/空图）就什么也不做，不猜
              const index = Number(state?.activeIndex);
              const id = Number.isInteger(index) ? rows[index]?.interview_id : undefined;
              if (typeof id === "string") onSelect(id);
            }}
          >
            <CartesianGrid stroke={tokens.border} vertical={false} />
            <XAxis
              dataKey="label"
              axisLine={false}
              tickLine={false}
              tick={{ fill: tokens.muted, fontSize: 12 }}
            />
            <YAxis
              domain={Y_DOMAIN}
              ticks={Y_TICKS}
              axisLine={false}
              tickLine={false}
              tick={{ fill: tokens.muted, fontSize: 11 }}
            />
            <Tooltip
              {...tooltipStyles(tokens)}
              formatter={(value) => [`${value} 分`, "总分"]}
            />
            <Line
              type="monotone"
              dataKey="overall"
              stroke={tokens.primary}
              strokeWidth={2}
              dot={{ r: 4, fill: tokens.primary, strokeWidth: 0 }}
              activeDot={{ r: 6 }}
              isAnimationActive={false}
              cursor="pointer"
            />
          </LineChart>
        </ResponsiveContainer>
      )}
    </ChartFrame>
  );
}

/**
 * 迷你趋势图（小倍图单元）：一项一张。
 *
 * `connectNulls={false}` 是**语义**不是样式：没考过的场次没有分，线在那里断开；
 * 连起来等于替用户编了一段「那场考了且得了这个分」的曲线。
 */
export function MiniTrend({
  title,
  rows,
  dataKey,
  footnote,
}: {
  title: string;
  rows: ProfileChartRow[];
  dataKey: string;
  footnote?: string | null;
}) {
  const { tokens, mounted } = useChartTokens();
  const hasValue = rows.some((row) => typeof row[dataKey] === "number");

  return (
    <div className="rounded-lg border p-3">
      <div className="mb-1 flex items-baseline justify-between gap-2">
        <span className="truncate text-sm">{title}</span>
        {footnote ? (
          <span className="shrink-0 text-xs text-muted-foreground">{footnote}</span>
        ) : null}
      </div>
      {hasValue ? (
        <ChartFrame height={96}>
          {mounted && (
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={rows} margin={{ top: 6, right: 6, bottom: 0, left: 6 }}>
                <XAxis dataKey="label" hide />
                <YAxis domain={Y_DOMAIN} ticks={Y_TICKS} hide />
                <Tooltip
                  {...tooltipStyles(tokens)}
                  formatter={(value) => [value === null ? "该场未考" : `${value} 分`, title]}
                />
                <Line
                  type="monotone"
                  dataKey={dataKey}
                  stroke={tokens.primary}
                  strokeWidth={2}
                  dot={{ r: 3, fill: tokens.primary, strokeWidth: 0 }}
                  connectNulls={false}
                  isAnimationActive={false}
                />
              </LineChart>
            </ResponsiveContainer>
          )}
        </ChartFrame>
      ) : (
        // 不静默留白：从来没考过的域要说清楚，否则用户以为图表坏了
        <p className="py-6 text-center text-xs text-muted-foreground">尚未考过</p>
      )}
    </div>
  );
}
