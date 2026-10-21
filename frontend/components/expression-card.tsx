import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  formatSpeechDuration,
  formatSpeechRate,
  type ExpressionSummary,
} from "@/lib/expression";

/**
 * 报告页「表达复盘」卡（M16）：语速 / 长停顿 / 口头禅 / 语音作答时长。
 *
 * 刻意**不依赖 hasScores**：评分降级（未评分）的场次同样有语音指标，
 * 整块分数区收起时这张卡仍要显示。指标全部由后端派生，前端只格式化。
 */
export function ExpressionCard({ expression }: { expression: ExpressionSummary }) {
  const stats = [
    {
      label: "语音作答时长",
      value: formatSpeechDuration(expression.duration_ms),
      hint: `${expression.spoken_question_count} 题含语音`,
    },
    {
      label: "平均语速",
      value: formatSpeechRate(expression.chars_per_min),
      hint: `共 ${expression.chars} 字`,
    },
    {
      label: "长停顿（≥2s）",
      value: `${expression.pause_count} 次`,
      hint: `共 ${formatSpeechDuration(expression.pause_ms)}`,
    },
    {
      label: "口头禅",
      value: `${expression.filler_count} 次`,
      hint: "转写中检出",
    },
  ];

  return (
    <Card>
      <CardHeader>
        <CardTitle>表达复盘</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        <dl className="grid grid-cols-2 gap-x-6 gap-y-4 sm:grid-cols-4">
          {stats.map((stat) => (
            <div key={stat.label} className="flex flex-col gap-1">
              <dt className="text-xs text-muted-foreground">{stat.label}</dt>
              <dd className="text-lg font-medium tabular-nums">{stat.value}</dd>
              <dd className="text-xs text-muted-foreground">{stat.hint}</dd>
            </div>
          ))}
        </dl>
        <p className="text-xs leading-relaxed text-muted-foreground">{expression.note}</p>
      </CardContent>
    </Card>
  );
}
