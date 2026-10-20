import type { Phase } from "@/lib/api";

/**
 * 面试间左栏的阶段进度。
 *
 * 技术面与行为面的中间阶段名不同（tech_base / project / behavioral），但用户看到的
 * 都是「问答」这一段；合并成 5 步，两种会话类型共用一套时间线。
 * 纯函数：当前阶段 → 步骤列表与当前下标，单测钉死。
 */
export type PhaseStep = {
  key: "intro" | "warmup" | "qa" | "closing" | "finished";
  label: string;
};

const STEPS: PhaseStep[] = [
  { key: "intro", label: "开场" },
  { key: "warmup", label: "自我介绍" },
  { key: "qa", label: "问答" },
  { key: "closing", label: "反问" },
  { key: "finished", label: "报告" },
];

const INDEX: Record<Phase, number> = {
  intro: 0,
  warmup: 1,
  tech_base: 2,
  project: 2,
  behavioral: 2,
  closing: 3,
  finished: 4,
};

export function phaseSteps(phase: Phase): {
  steps: PhaseStep[];
  currentIndex: number;
  finished: boolean;
} {
  const currentIndex = INDEX[phase] ?? 0;
  return {
    steps: STEPS,
    currentIndex,
    finished: phase === "finished",
  };
}
