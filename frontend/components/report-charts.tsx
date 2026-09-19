"use client";

import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  LabelList,
  PolarAngleAxis,
  PolarGrid,
  PolarRadiusAxis,
  Radar,
  RadarChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { useChartTokens, type ChartTokens } from "@/lib/chart-tokens";
import { DIMENSIONS } from "@/lib/constants";
import { formatScore } from "@/lib/format";

/** 图表占位：ResponsiveContainer 需要真实 DOM 尺寸，首帧渲染同高容器避免跳动。 */
function ChartFrame({ height, children }: { height: number; children: React.ReactNode }) {
  return <div style={{ width: "100%", height }}>{children}</div>;
}

function tooltipStyles(tokens: ChartTokens) {
  return {
    contentStyle: {
      background: tokens.surface,
      border: `1px solid ${tokens.border}`,
      borderRadius: 8,
      fontSize: 12,
      padding: "6px 10px",
    },
    labelStyle: { color: tokens.muted, marginBottom: 2 },
    itemStyle: { color: tokens.foreground },
  };
}

/** 五维能力雷达：单序列，维度名即标签，无需图例。 */
export function ScoreRadar({ scores }: { scores: Record<string, number> }) {
  const { tokens, mounted } = useChartTokens();
  const data = DIMENSIONS.map((dim) => ({
    dimension: dim.label,
    score: scores[dim.key] ?? 0,
  }));

  return (
    <ChartFrame height={288}>
      {mounted && (
        <ResponsiveContainer width="100%" height="100%">
          <RadarChart data={data} outerRadius="70%" margin={{ top: 8, right: 24, bottom: 8, left: 24 }}>
            <PolarGrid stroke={tokens.border} />
            <PolarAngleAxis
              dataKey="dimension"
              tick={{ fill: tokens.muted, fontSize: 12 }}
            />
            <PolarRadiusAxis
              domain={[0, 5]}
              tickCount={6}
              axisLine={false}
              tick={{ fill: tokens.muted, fontSize: 10 }}
            />
            <Radar
              dataKey="score"
              stroke={tokens.primary}
              strokeWidth={2}
              fill={tokens.primary}
              fillOpacity={0.22}
              dot={{ r: 4, fill: tokens.primary, strokeWidth: 0 }}
              isAnimationActive={false}
            />
            <Tooltip {...tooltipStyles(tokens)} formatter={(value) => [`${value} 分`, "得分"]} />
          </RadarChart>
        </ResponsiveContainer>
      )}
    </ChartFrame>
  );
}

/** 知识域均分：横向条形，短板域换语义色并附文字标注（不依赖颜色单独表意）。 */
export function DomainBars({
  domainScores,
  weaknesses,
  labels,
}: {
  domainScores: Record<string, number>;
  weaknesses: string[];
  labels: Record<string, string>;
}) {
  const { tokens, mounted } = useChartTokens();
  const weakSet = new Set(weaknesses);
  const data = Object.entries(domainScores)
    .map(([key, score]) => ({
      key,
      label: labels[key] ?? key,
      score,
      weak: weakSet.has(key),
    }))
    .sort((a, b) => b.score - a.score);

  const maxLabel = data.reduce((longest, item) => Math.max(longest, item.label.length), 0);

  return (
    <ChartFrame height={Math.max(180, data.length * 44)}>
      {mounted && (
        <ResponsiveContainer width="100%" height="100%">
          <BarChart
            data={data}
            layout="vertical"
            margin={{ top: 4, right: 44, bottom: 4, left: 8 }}
            barCategoryGap={10}
          >
            <CartesianGrid horizontal={false} stroke={tokens.border} />
            <XAxis
              type="number"
              domain={[0, 5]}
              ticks={[0, 1, 2, 3, 4, 5]}
              axisLine={false}
              tickLine={false}
              tick={{ fill: tokens.muted, fontSize: 11 }}
            />
            <YAxis
              type="category"
              dataKey="label"
              width={Math.min(180, maxLabel * 13 + 44)}
              axisLine={false}
              tickLine={false}
              tick={{ fill: tokens.foreground, fontSize: 12 }}
            />
            <Tooltip
              {...tooltipStyles(tokens)}
              cursor={{ fill: tokens.border, fillOpacity: 0.35 }}
              formatter={(value, _name, item) => [
                `${value} 分${item?.payload?.weak ? "（短板）" : ""}`,
                "均分",
              ]}
            />
            <Bar dataKey="score" radius={[0, 4, 4, 0]} barSize={18} isAnimationActive={false}>
              {data.map((item) => (
                <Cell
                  key={item.key}
                  fill={item.weak ? tokens.warning : tokens.primary}
                />
              ))}
              <LabelList
                dataKey="score"
                position="right"
                offset={8}
                formatter={(value) => (typeof value === "number" ? formatScore(value) : "")}
                fill={tokens.foreground}
                fontSize={12}
              />
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      )}
    </ChartFrame>
  );
}
