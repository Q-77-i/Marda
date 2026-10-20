import Link from "next/link";

import { LoginForm } from "@/components/login-form";

const SPECS = [
  {
    index: "01",
    title: "确定性编排",
    body: "阶段推进、轮数上限与追问决策由代码判定，流程可解释、可回放。",
  },
  {
    index: "02",
    title: "语义评分",
    body: "五维评分与关键点覆盖逐题留痕，评分口径全场一致。",
  },
  {
    index: "03",
    title: "能力档案",
    body: "跨场次曲线、知识域热力图与短板变化，看清在变好还是变差。",
  },
  {
    index: "04",
    title: "学习闭环",
    body: "按报告暴露的短板域检索题目，练完接着补，不停在分数上。",
  },
];

const TRACE = [
  { step: "ASK", copy: "第 5 题 / RAG / L2", tag: "出题" },
  { step: "JUDGE", copy: "关键点覆盖不足 / 未发现明确错误", tag: "评分" },
  { step: "DECIDE", copy: "追问 / 遗漏点 2 个", tag: "决策" },
];

export default function LoginPage() {
  return (
    <div className="grid min-h-[100dvh] lg:grid-cols-[1.06fr_0.94fr]">
      <section className="rail-texture relative flex flex-col bg-rail px-6 py-6 text-rail-foreground lg:px-12 lg:py-8">
        <div className="flex items-start justify-between gap-5">
          <Link href="/" aria-label="Marda 码达" className="flex items-center gap-2.5">
            <span className="flex size-8 items-center justify-center rounded-lg bg-primary text-sm font-bold tracking-[-0.05em] text-primary-foreground">
              M
            </span>
            <span className="flex flex-col leading-none">
              <span className="text-[13.5px] font-semibold tracking-[-0.01em] text-white">
                Marda 码达
              </span>
              <span className="mt-1 font-mono text-[9px] tracking-[0.14em] text-rail-muted uppercase">
                Agent Interview Engine
              </span>
            </span>
          </Link>
          <div className="text-right font-mono text-[9px] leading-relaxed tracking-[0.12em] text-[#6f737a] uppercase">
            校招 / Agent/AI 工程师
            <br />
            Interview practice / 2026
          </div>
        </div>

        <div className="my-auto max-w-[660px] py-10 lg:py-12">
          <div className="mb-4 flex items-center gap-2 font-mono text-[9px] tracking-[0.16em] text-[#8ba1ff] uppercase">
            <span aria-hidden className="h-px w-5 bg-[#8ba1ff]" />
            Interview engine / 01
          </div>
          <h1 className="text-[clamp(2.25rem,4.6vw,4.1rem)] leading-[1.06] font-semibold tracking-[-0.045em] text-[#f2f0ea]">
            练完，
            <br />
            <span className="text-[#9bb0ff]">知道差在哪。</span>
          </h1>
          <p className="mt-5 max-w-[34em] text-sm leading-7 text-[#a3a7ad]">
            Agent 智能面试引擎：自主出题、动态追问，面完给出短板定位与下一步。
            <br className="hidden sm:block" />
            不是题库抽题，是一条可复盘、可解释的训练路径。
          </p>

          <div className="mt-6 hidden border-y border-rail-line lg:block">
            <div className="flex items-center justify-between py-2.5 font-mono text-[9px] tracking-[0.12em] text-[#6f737a] uppercase">
              <span>Decision trace / sample</span>
              <span>逐轮可回放</span>
            </div>
            {TRACE.map((row) => (
              <div
                key={row.step}
                className="grid grid-cols-[54px_minmax(0,1fr)_44px] gap-2.5 border-t border-white/6 py-2 font-mono text-[10px] leading-relaxed"
              >
                <span className="tracking-[0.06em] text-[#8ba1ff]">{row.step}</span>
                <span className="min-w-0 text-[#b9bcc2]">{row.copy}</span>
                <span className="text-right text-[9px] tracking-[0.08em] text-[#6f737a] uppercase">
                  {row.tag}
                </span>
              </div>
            ))}
          </div>
        </div>

        <div className="hidden border-y border-rail-line sm:grid sm:grid-cols-2">
          {SPECS.map((spec, index) => (
            <div
              key={spec.index}
              className={[
                "py-3.5 pr-4",
                index % 2 === 1 ? "border-l border-rail-line pl-4 pr-0" : "",
                index >= 2 ? "border-t border-rail-line" : "",
              ].join(" ")}
            >
              <div className="flex items-center gap-2 font-mono text-[9px] tracking-[0.12em] text-[#7d8188] uppercase">
                <b className="font-medium text-[#8ba1ff]">{spec.index}</b>
                {spec.title}
              </div>
              <p className="mt-1.5 text-[11.5px] leading-relaxed text-[#a7abb1]">
                {spec.body}
              </p>
            </div>
          ))}
        </div>

        <div className="mt-5 hidden items-center justify-between font-mono text-[9px] tracking-[0.12em] text-[#6f737a] uppercase lg:flex">
          <span>题库 / 五维评分 / 决策回放</span>
          <span>Agent/AI 工程师</span>
        </div>
      </section>

      <section className="relative flex flex-col bg-card px-6 py-6 lg:px-12 lg:py-8">
        <div className="flex items-center justify-between font-mono text-[9px] tracking-[0.14em] text-muted-foreground uppercase">
          <span>Marda console / access</span>
          <span>Local demo</span>
        </div>

        <div className="mx-auto mt-14 w-full max-w-[378px] lg:mt-[12vh]">
          <LoginForm />
        </div>

        <div className="mt-auto flex items-center justify-between pt-8 font-mono text-[9px] tracking-[0.1em] text-muted-foreground uppercase">
          <span>Local SQLite / no third-party upload</span>
          <span>Marda 2026</span>
        </div>
      </section>
    </div>
  );
}
