"use client";

import { ChevronDown, ChevronUp, FileUp, Play, Target, X } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { MonoLabel } from "@/components/ui/mono-label";
import { Segmented } from "@/components/ui/segmented";
import { StatusBanner } from "@/components/ui/status-banner";
import { Textarea } from "@/components/ui/textarea";
import {
  createInterview,
  dispatcher,
  getBankCapacity,
  uploadResume,
  type CapacityOption,
} from "@/lib/api";
import { capacityFor, shortfallMessage } from "@/lib/bank";
import {
  BEHAVIORAL_MAX_QUESTIONS,
  DIFFICULTY_OPTIONS,
  INTERVIEW_TYPE_OPTIONS,
  POSITION,
  QUESTION_COUNT_OPTIONS,
  domainLabel,
  isBehavioral,
  questionCountOptions,
} from "@/lib/constants";
import {
  RESUME_ACCEPT,
  RESUME_DISCLOSURE,
  RESUME_PASTE_PLACEHOLDER,
  RESUME_SUFFIX_HINT,
  RESUME_TITLE,
  resumeDigest,
  resumeSource,
  type ResumeParseResult,
} from "@/lib/resume";

/**
 * 创建面试表单（决策 1A）：提交后跟完开场流再跳转面试页，
 * 等待期间把面试官开场白流式显示出来，避免"点了没反应"。
 *
 * 难度（P1-M6 FR-14）：自适应 / L1 / L2 / L3；题库直供不足的题量禁用并给出缺在哪。
 * 会话类型（P1-M11 FR-22）：技术面 / 行为面——行为面**隐藏难度与容量**（行为题的 L1-L3 是
 * 技术深度语义，题库也不按域配额供题），题量上限 10（池子只有十来道，再多当场耗尽走 LLM 兜底）。
 */
export function CreateForm() {
  const router = useRouter();
  const [interviewType, setInterviewType] = useState<string>(INTERVIEW_TYPE_OPTIONS[0].value);
  const [count, setCount] = useState<number>(10);
  const [difficulty, setDifficulty] = useState<string>(DIFFICULTY_OPTIONS[0].value);
  const [capacity, setCapacity] = useState<CapacityOption[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [opening, setOpening] = useState("");
  const [error, setError] = useState<string | null>(null);
  // 简历（P2-M11 FR-28）：解析成功才有 resumeId 随创建发出；改动输入即作废已解析结果
  const [resumeFile, setResumeFile] = useState<File | null>(null);
  const [resumeText, setResumeText] = useState("");
  const [resume, setResume] = useState<ResumeParseResult | null>(null);
  const [parsing, setParsing] = useState(false);
  const [resumeError, setResumeError] = useState<string | null>(null);
  const [fileInputKey, setFileInputKey] = useState(0); // 移除后清空文件框（同文件重选也能触发）
  const [resumeOpen, setResumeOpen] = useState(false); // 简历是可选段，默认收起，保证主操作在第一屏

  useEffect(() => {
    getBankCapacity([...QUESTION_COUNT_OPTIONS])
      .then((body) => setCapacity(body.options))
      .catch(() => setCapacity(null)); // 拉不到容量不禁用任何选项（服务端不拦，别自锁）
  }, []);

  const behavioral = isBehavioral(interviewType);
  const countOptions = questionCountOptions(interviewType);
  // 行为面直接跳过容量判定：容量按「难度 × 域配额」算，与行为面题源无关（服务端也不拦）
  const blocked = behavioral
    ? []
    : countOptions
        .map((option) => ({
          count: option,
          reason: shortfallMessage(capacityFor(capacity, difficulty, option), domainLabel),
        }))
        .filter((item) => item.reason !== null);
  const currentBlocked = blocked.some((item) => item.count === count);

  function pickType(value: string) {
    setInterviewType(value);
    setError(null);
    // 行为面题量上限 10：从技术面切过来时把 15 收回来，否则会撞 422
    if (isBehavioral(value) && count > BEHAVIORAL_MAX_QUESTIONS) setCount(BEHAVIORAL_MAX_QUESTIONS);
  }

  function pickResumeFile(file: File | null) {
    setResumeFile(file);
    setResume(null); // 输入变了，上次的解析结果作废（否则会带着旧简历开场）
    setResumeError(null);
  }

  function editResumeText(value: string) {
    setResumeText(value);
    setResume(null);
    setResumeError(null);
  }

  function clearResume() {
    setResumeFile(null);
    setResumeText("");
    setResume(null);
    setResumeError(null);
    setFileInputKey((key) => key + 1);
  }

  async function handleParseResume() {
    const source = resumeSource(resumeFile, resumeText);
    if (!source) return;
    setParsing(true);
    setResumeError(null);
    try {
      setResume(await uploadResume(source === "file" ? resumeFile : null, resumeText));
    } catch (err) {
      // 解析失败明确报错（并把出路写在服务端 detail 里：重试 / 改用粘贴文本）
      setResumeError(err instanceof Error ? err.message : "简历解析失败，请重试");
    } finally {
      setParsing(false);
    }
  }

  async function handleStart() {
    setBusy(true);
    setError(null);
    setOpening("");
    let streamed = false; // 当前这条面试官消息是否收到过分片（决定终稿要不要再追加）
    try {
      const interviewId = await createInterview(
        POSITION,
        count,
        // 行为面不消费难度（D3）：仍发 adaptive，state 里照常自适应但只作死数据
        behavioral ? DIFFICULTY_OPTIONS[0].value : difficulty,
        dispatcher({
          // 流式（P2-M4）：开场逐片显示；终稿（delta）只在**没有分片**时才追加，
          // 否则同一段文案会被写两遍（这里只是等待期的预览，不必做终稿对账替换）
          delta_start: () => {
            streamed = false;
          },
          delta_chunk: ({ text }) => {
            streamed = true;
            setOpening((prev) => prev + text);
          },
          delta: ({ text }) => {
            if (streamed) return;
            setOpening((prev) => prev + text);
          },
          error: ({ message }) => setError(message),
        }),
        interviewType,
        resume?.resume_id ?? null,
      );
      router.push(`/interview/${interviewId}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "创建失败，请重试");
      setBusy(false);
    }
  }

  const typeOptions = INTERVIEW_TYPE_OPTIONS.map((option) => ({
    value: option.value,
    label: option.label,
    title: option.hint,
  }));

  const difficultyOptions = DIFFICULTY_OPTIONS.map((option) => ({
    value: option.value,
    label: option.value === "adaptive" ? "自适应" : option.value,
    title: `${option.label}：${option.hint}`,
  }));

  const countChoices = countOptions.map((option) => {
    const reason = blocked.find((item) => item.count === option)?.reason ?? null;
    return {
      value: String(option),
      label: `${option} 题`,
      title: reason ?? undefined,
      disabled: reason !== null,
    };
  });

  return (
    <Card>
      <CardHeader>
        <CardTitle>新建模拟面试</CardTitle>
        <MonoLabel>New session</MonoLabel>
      </CardHeader>
      <CardContent className="flex flex-col gap-3.5">
        <div className="flex flex-col gap-2">
          <MonoLabel>岗位方向</MonoLabel>
          <div className="flex h-10 items-center gap-2.5 rounded-lg border border-border bg-muted/40 px-3 text-[13px]">
            <Target className="size-4 text-muted-foreground" strokeWidth={1.8} />
            {POSITION}
            <span className="ml-auto font-mono text-[9px] tracking-[0.08em] text-muted-foreground uppercase">
              Fixed
            </span>
          </div>
        </div>

        <div className="flex flex-col gap-2">
          <MonoLabel>面试类型</MonoLabel>
          <Segmented
            options={typeOptions}
            value={interviewType}
            onChange={pickType}
            columns={2}
            ariaLabel="面试类型"
            disabled={busy}
          />
          <span className="text-[11px] text-muted-foreground">
            {INTERVIEW_TYPE_OPTIONS.find((option) => option.value === interviewType)?.hint}
          </span>
        </div>

        {!behavioral && (
          <div className="flex flex-col gap-2">
            <MonoLabel>难度</MonoLabel>
            <Segmented
              options={difficultyOptions}
              value={difficulty}
              onChange={setDifficulty}
              columns={4}
              ariaLabel="难度"
              disabled={busy}
            />
            <span className="text-[11px] text-muted-foreground">
              {DIFFICULTY_OPTIONS.find((option) => option.value === difficulty)?.hint}
            </span>
          </div>
        )}

        <div className="flex flex-col gap-2">
          <MonoLabel>题目数量</MonoLabel>
          <Segmented
            options={countChoices}
            value={String(count)}
            onChange={(value) => setCount(Number(value))}
            columns={3}
            ariaLabel="题目数量"
            disabled={busy}
          />
          {blocked.map((item) => (
            <span key={item.count} className="text-[11px] text-destructive" role="status">
              {item.count} 题不可选：{item.reason}
            </span>
          ))}
        </div>

        <div className="flex flex-col gap-2">
          <button
            type="button"
            onClick={() => setResumeOpen((value) => !value)}
            aria-expanded={resumeOpen}
            className="flex h-9 items-center justify-between gap-2 rounded-lg border border-border bg-muted/30 px-3 text-left transition-colors hover:border-foreground/30 hover:bg-muted"
          >
            <span className="flex min-w-0 items-center gap-2 text-muted-foreground">
              <FileUp className="size-3.5 shrink-0" strokeWidth={1.8} />
              <MonoLabel className="truncate text-[10px]" title={RESUME_TITLE}>导入简历（可选）</MonoLabel>
            </span>
            <span className="flex shrink-0 items-center gap-1.5 font-mono text-[9px] tracking-[0.06em] text-muted-foreground uppercase">
              {resume ? "已解析" : resumeOpen ? "收起" : "添加"}
              {resumeOpen ? (
                <ChevronUp className="size-3.5" strokeWidth={1.8} />
              ) : (
                <ChevronDown className="size-3.5" strokeWidth={1.8} />
              )}
            </span>
          </button>

          {resume ? (
            <div className="flex items-center justify-between gap-2 rounded-lg border border-border bg-muted/40 px-3 py-2">
              <span className="min-w-0 truncate text-xs">{resumeDigest(resume)}</span>
              <Button
                type="button"
                variant="ghost"
                size="icon-sm"
                disabled={busy}
                onClick={clearResume}
                aria-label="移除已解析的简历"
              >
                <X className="size-3.5" />
              </Button>
            </div>
          ) : resumeOpen ? (
            <div className="flex flex-col gap-2">
              <label className="flex cursor-pointer flex-col items-center justify-center gap-1 rounded-lg border border-dashed border-input bg-muted/40 px-3 py-3 text-center transition-colors hover:border-primary hover:bg-accent">
                <FileUp className="size-4 text-muted-foreground" strokeWidth={1.8} />
                <span className="text-xs font-medium">点击选择文件</span>
                <span className="text-[10.5px] text-muted-foreground">
                  支持 .pdf / .md / .txt，不超过 2MB
                </span>
                <input
                  key={fileInputKey}
                  type="file"
                  accept={RESUME_ACCEPT}
                  disabled={busy || parsing}
                  aria-label="选择简历文件"
                  className="sr-only"
                  onChange={(event) => pickResumeFile(event.target.files?.[0] ?? null)}
                />
              </label>
              <Textarea
                value={resumeText}
                onChange={(event) => editResumeText(event.target.value)}
                placeholder={RESUME_PASTE_PLACEHOLDER}
                disabled={busy || parsing}
                rows={2}
              />
              <div className="flex items-center gap-2">
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  disabled={busy || parsing || resumeSource(resumeFile, resumeText) === null}
                  onClick={handleParseResume}
                >
                  {parsing ? "正在解析…" : "解析简历"}
                </Button>
                <span className="text-[10.5px] text-muted-foreground">{RESUME_SUFFIX_HINT}</span>
              </div>
            </div>
          ) : null}

          {resumeOpen || resume ? (
            <span className="text-[10.5px] leading-relaxed text-muted-foreground">
              {RESUME_DISCLOSURE}
            </span>
          ) : null}
          {resumeError && (
            <p className="text-xs text-destructive" role="alert">
              {resumeError}
            </p>
          )}
        </div>

        <Button
          size="lg"
          className="mt-1 w-full justify-between px-4"
          disabled={busy || currentBlocked}
          onClick={handleStart}
        >
          <span className="flex items-center gap-2">
            <Play className="size-4" strokeWidth={1.8} />
            {busy ? "正在准备面试…" : "开始面试"}
          </span>
          <span className="font-mono text-[9px] tracking-[0.08em] opacity-70">
            {count} Q
          </span>
        </Button>

        {busy && (
          <StatusBanner className="items-start py-3 leading-relaxed">
            {opening || "面试官正在准备开场…"}
          </StatusBanner>
        )}

        {error && (
          <p className="text-xs text-destructive" role="alert">
            {error}
          </p>
        )}
      </CardContent>
    </Card>
  );
}
