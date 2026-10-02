"use client";

import { Fragment } from "react";
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
import { domainLabel } from "@/lib/constants";
import { formatScore } from "@/lib/format";
import { axisTicks, heatLevel, type HeatRow, type ProfileChartRow } from "@/lib/profile";
import { cn } from "@/lib/utils";

/**
 * 能力档案图表（FR-19 / P1-M10.5）。
 *
 * 形态选择：总分用一张主曲线；知识域用**热力图**（行 = 域、列 = 场次、格 = 色 + 数字）——
 * 小倍图（每域一张迷你曲线）在真数据上暴露了硬伤：**各图 X 轴刻度不对齐**，
 * 6 张形状不同的小图之间无法横向比较「第 3 场里哪个域最强」。热力图天然解决这一点
 * （行 = 单域走向、列 = 单场横截面），且没有「断线」这个视觉问题，缺场格直接写「未考」。
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
              // 标签只标「一天一个」（同日多场靠 tooltip 与热力图列头区分），
              // 点为数据本身、不受 ticks 影响：横轴只控制标签密度
              ticks={axisTicks(rows.map((row) => String(row.label)))}
              axisLine={false}
              tickLine={false}
              tick={{ fill: tokens.muted, fontSize: 12 }}
            />
            <YAxis
              domain={Y_DOMAIN}
              ticks={Y_TICKS}
              axisLine={false}
              tickLine={false}
              tick={{ fill: tokens.muted, fontSize: 12 }}
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
 * 知识域热力图：行 = 知识域、列 = 场次、单元格 = 色 + 数字；缺场写「未考」。
 *
 * 用 CSS grid 而不是 recharts：后者没有热力图原语，用 Cell 拼是 hack；
 * CSS grid 直接吃 CSS 变量（明暗自适应，不必等挂载后读令牌）、缺场格与窄屏横向滚动
 * 都是天然的。**颜色只是副渠道**——每格都印数字、缺场格印「未考」，
 * 色阶只负责「一眼看出高低」，不单独表意（同报告页 DomainBars 的口径）。
 */
export function DomainHeatmap({ rows, labels }: { rows: HeatRow[]; labels: string[] }) {
  return (
    <div className="flex flex-col gap-3">
      <div className="overflow-x-auto">
        <div
          className="grid min-w-[520px] gap-1"
          style={{
            gridTemplateColumns: `minmax(6.5rem, 9rem) repeat(${labels.length}, minmax(3.25rem, 1fr))`,
          }}
        >
          <div aria-hidden />
          {labels.map((label) => (
            <div key={label} className="pb-1 text-center text-xs text-muted-foreground">
              {label}
            </div>
          ))}
          {rows.map((row) => (
            <Fragment key={row.domain}>
              <div className="flex items-center pr-2 text-xs leading-snug text-muted-foreground">
                {domainLabel(row.domain)}
              </div>
              {row.cells.map((value, index) => {
                const level = heatLevel(value);
                const title =
                  value === null
                    ? `${labels[index]} · ${domainLabel(row.domain)} · 未考`
                    : `${labels[index]} · ${domainLabel(row.domain)} · ${formatScore(value)} 分`;
                return (
                  <div
                    key={labels[index]}
                    title={title}
                    className={cn(
                      "flex h-9 items-center justify-center rounded-[4px] text-xs tabular-nums",
                      // 缺场不静默留白：写「未考」并虚线描边——「没有数据」与「低分」必须一眼分开
                      value === null && "border border-dashed border-border text-muted-foreground",
                    )}
                    style={
                      level === null
                        ? undefined
                        : { background: `var(--heat-${level})`, color: `var(--heat-fg-${level})` }
                    }
                  >
                    {value === null ? "未考" : formatScore(value)}
                  </div>
                );
              })}
            </Fragment>
          ))}
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
        <span className="flex items-center gap-1">
          低
          {[1, 2, 3, 4, 5].map((level) => (
            <span
              key={level}
              className="h-3.5 w-6 rounded-[3px]"
              style={{ background: `var(--heat-${level})` }}
            />
          ))}
          高
        </span>
        <span className="flex items-center gap-1">
          <span className="h-3.5 w-6 rounded-[3px] border border-dashed border-border" />
          未考 = 该场没有考到这个域
        </span>
      </div>
    </div>
  );
}
