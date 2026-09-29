"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import {
  getPrivateQuestions,
  patchPrivateQuestion,
  uploadPrivateFile,
  type PrivateListResponse,
  type PrivateQuestion,
  type PrivateQuestionPatch,
  type UploadReport,
} from "@/lib/api";
import { totalPages } from "@/lib/bank";
import { DIFFICULTY_OPTIONS, ENABLED_DOMAINS, domainLabel, difficultyLabel } from "@/lib/constants";
import {
  EMPTY_PRIVATE_FILTERS,
  STATUS_OPTIONS,
  TEMPLATE_HINT,
  fileRejectReason,
  itemsToLines,
  linesToItems,
  privateStatusLabel,
  toPrivateQuery,
  uploadSummary,
  withPrivateFilter,
  type PrivateFilters,
} from "@/lib/private-bank";

const PAGE_SIZE = 10;

/** 上传/编辑可选难度：adaptive 是**面试模式**（引擎从 L1 起升降），不是题目属性。 */
const BANK_DIFFICULTIES = DIFFICULTY_OPTIONS.filter((option) => option.value !== "adaptive");

/**
 * 我的题库（FR-13）：上传 → 管理 → 混入出题。
 *
 * 上传是部分成功语义，结果条必须同时给三份数（导入/重复/失败）——只报「成功 2 条」
 * 会让用户以为剩下几条被吞了；重复题特意写明「未覆盖」，因为答案可能已被他改过。
 * 编辑走**行内展开**而非弹层：与题库页的展开看答案同一套交互，不额外引入 dialog。
 */
export function PrivateBankClient() {
  const [filters, setFilters] = useState<PrivateFilters>(EMPTY_PRIVATE_FILTERS);
  const [data, setData] = useState<PrivateListResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);

  const [file, setFile] = useState<File | null>(null);
  const [domain, setDomain] = useState<string>(ENABLED_DOMAINS[0]);
  const [difficulty, setDifficulty] = useState("L2");
  const [uploading, setUploading] = useState(false);
  const [report, setReport] = useState<UploadReport | null>(null);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  const load = useCallback(async (next: PrivateFilters) => {
    setLoading(true);
    setError(null);
    try {
      setData(await getPrivateQuestions(toPrivateQuery(next, PAGE_SIZE)));
    } catch (err) {
      setData(null);
      setError(err instanceof Error ? err.message : "题库加载失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load(filters);
  }, [filters, load]);

  function pickFile(next: File | null) {
    setReport(null);
    if (!next) {
      setFile(null);
      setUploadError(null);
      return;
    }
    const reason = fileRejectReason(next);
    setFile(reason ? null : next);
    setUploadError(reason);
  }

  async function doUpload(event: React.FormEvent) {
    event.preventDefault();
    if (!file || uploading) return;
    setUploading(true);
    setUploadError(null);
    try {
      const result = await uploadPrivateFile(file, domain, difficulty);
      setReport(result);
      setFilters(EMPTY_PRIVATE_FILTERS); // 新题按 rowid 倒序在最前，回第一页才看得到
      await load(EMPTY_PRIVATE_FILTERS);
      if (result.imported > 0) {
        setFile(null);
        if (fileInput.current) fileInput.current.value = ""; // 清空才能重选同一个文件
      }
    } catch (err) {
      setReport(null);
      setUploadError(err instanceof Error ? err.message : "上传失败");
    } finally {
      setUploading(false);
    }
  }

  /** 编辑保存 / 归档恢复：返回更新后的整条，就地替换（不重载，避免翻页位置丢失）。 */
  async function applyPatch(questionId: string, patch: PrivateQuestionPatch) {
    try {
      const updated = await patchPrivateQuestion(questionId, patch);
      setData((prev) =>
        prev
          ? { ...prev, items: prev.items.map((i) => (i.question_id === questionId ? updated : i)) }
          : prev,
      );
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "保存失败");
    }
  }

  const pages = totalPages(data?.total ?? null, PAGE_SIZE);
  const summary = report ? uploadSummary(report) : null;

  return (
    <div className="flex flex-col gap-6">
      <Card>
        <CardHeader>
          <CardTitle>上传我的题目</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          <p className="text-sm text-muted-foreground">
            传你自己整理的题目（md / txt / pdf，≤ 2MB），它们
            <strong className="font-medium text-foreground">只对你可见</strong>
            ，并会混进你的模拟面试出题池。
          </p>

          <form className="flex flex-col gap-3" onSubmit={doUpload}>
            <input
              ref={fileInput}
              type="file"
              accept=".md,.markdown,.txt,.pdf"
              aria-label="选择题目文件"
              onChange={(event) => pickFile(event.target.files?.[0] ?? null)}
              className="text-sm file:mr-3 file:rounded-md file:border file:bg-transparent file:px-3 file:py-1.5 file:text-sm"
            />

            <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
              <label className="flex items-center gap-2 text-sm">
                <span className="w-12 shrink-0 text-muted-foreground">知识域</span>
                <select
                  className="h-9 w-full rounded-md border bg-transparent px-2 text-sm"
                  value={domain}
                  onChange={(event) => setDomain(event.target.value)}
                >
                  {ENABLED_DOMAINS.map((value) => (
                    <option key={value} value={value}>
                      {domainLabel(value)}
                    </option>
                  ))}
                </select>
              </label>
              <label className="flex items-center gap-2 text-sm">
                <span className="w-12 shrink-0 text-muted-foreground">难度</span>
                <select
                  className="h-9 w-full rounded-md border bg-transparent px-2 text-sm"
                  value={difficulty}
                  onChange={(event) => setDifficulty(event.target.value)}
                >
                  {BANK_DIFFICULTIES.map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </select>
              </label>
            </div>
            <p className="text-xs text-muted-foreground">
              域与难度是整份文件的默认值（文件里不写内部枚举），入库后可逐题改。
            </p>

            <Button type="submit" disabled={!file || uploading} className="self-start">
              {uploading ? "上传中…" : "上传"}
            </Button>
          </form>

          {uploadError ? (
            <p className="text-sm text-destructive" role="alert">
              {uploadError}
            </p>
          ) : null}

          {report && summary ? (
            <UploadReportBar report={report} tone={summary.tone} text={summary.text} />
          ) : null}

          <details className="text-sm">
            <summary className="cursor-pointer text-muted-foreground">格式说明</summary>
            <pre className="mt-2 overflow-x-auto rounded-lg border bg-muted/30 p-3 text-xs leading-relaxed">
              {TEMPLATE_HINT}
            </pre>
            <p className="mt-2 text-xs text-muted-foreground">
              每道题以【题目】开头，【答案】必填（少于 5 个实质字符不算答案）；
              【关键点】【追问】可选，用分号或换行分开。一条坏题不影响其余题目入库。
            </p>
          </details>
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="flex-row items-center justify-between gap-4">
          <CardTitle className="text-base">我的题目</CardTitle>
          <span className="text-xs text-muted-foreground">
            {data ? `共 ${data.total} 题 · 第 ${data.page}/${pages} 页` : ""}
          </span>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
            <Select
              label="状态"
              value={filters.status}
              options={[...STATUS_OPTIONS]}
              onChange={(value) => setFilters((prev) => withPrivateFilter(prev, { status: value }))}
            />
            <Select
              label="知识域"
              value={filters.domain}
              options={[
                { value: "", label: "全部" },
                ...ENABLED_DOMAINS.map((value) => ({ value, label: domainLabel(value) })),
              ]}
              onChange={(value) => setFilters((prev) => withPrivateFilter(prev, { domain: value }))}
            />
            <Select
              label="难度"
              value={filters.difficulty}
              options={[
                { value: "", label: "全部" },
                ...BANK_DIFFICULTIES.map((o) => ({ value: o.value, label: o.label })),
              ]}
              onChange={(value) =>
                setFilters((prev) => withPrivateFilter(prev, { difficulty: value }))
              }
            />
          </div>

          {loading ? (
            <div className="flex flex-col gap-3">
              <Skeleton className="h-16 w-full" />
              <Skeleton className="h-16 w-full" />
              <Skeleton className="h-16 w-full" />
            </div>
          ) : error ? (
            <div className="flex flex-col items-start gap-2 py-6">
              <p className="text-sm text-destructive" role="alert">
                {error}
              </p>
              <Button variant="outline" size="sm" onClick={() => void load(filters)}>
                重试
              </Button>
            </div>
          ) : !data || data.items.length === 0 ? (
            <div className="flex flex-col items-center gap-1 py-12 text-center">
              <p className="text-sm font-medium">还没有题目</p>
              <p className="text-sm text-muted-foreground">
                {filters.status || filters.domain || filters.difficulty
                  ? "当前筛选下没有题目，换个条件看看"
                  : "上传一份你自己的题库文件，它们会参与出题"}
              </p>
            </div>
          ) : (
            <ul className="divide-y">
              {data.items.map((item) => (
                <PrivateRow
                  key={item.question_id}
                  item={item}
                  open={expanded === item.question_id}
                  onToggle={() =>
                    setExpanded(expanded === item.question_id ? null : item.question_id)
                  }
                  onSave={(patch) => applyPatch(item.question_id, patch)}
                />
              ))}
            </ul>
          )}

          {!loading && !error && data && data.total > PAGE_SIZE ? (
            <div className="flex items-center justify-between gap-2">
              <Button
                variant="outline"
                size="sm"
                disabled={data.page <= 1}
                onClick={() => setFilters((prev) => ({ ...prev, page: prev.page - 1 }))}
              >
                上一页
              </Button>
              <span className="tabular text-xs text-muted-foreground">
                {data.page} / {pages}
              </span>
              <Button
                variant="outline"
                size="sm"
                disabled={data.page >= pages}
                onClick={() => setFilters((prev) => ({ ...prev, page: prev.page + 1 }))}
              >
                下一页
              </Button>
            </div>
          ) : null}
        </CardContent>
      </Card>
    </div>
  );
}

/** 上传结果条：一行汇总 + 逐条明细（重复的题干 / 失败的原因）。 */
function UploadReportBar({
  report,
  tone,
  text,
}: {
  report: UploadReport;
  tone: "ok" | "warn" | "none";
  text: string;
}) {
  const color =
    tone === "ok"
      ? "border-emerald-500/40 bg-emerald-500/5"
      : tone === "warn"
        ? "border-amber-500/40 bg-amber-500/5"
        : "border-border bg-muted/30";
  return (
    <div className={`flex flex-col gap-2 rounded-lg border p-3 text-sm ${color}`} role="status">
      <p className="font-medium">{text}</p>
      {report.duplicated.length > 0 ? (
        <details>
          <summary className="cursor-pointer text-xs text-muted-foreground">
            已跳过 {report.duplicated.length} 道重复题（保留你改过的版本）
          </summary>
          <ul className="mt-1 flex flex-col gap-0.5 text-xs text-muted-foreground">
            {report.duplicated.map((question) => (
              <li key={question}>· {question}</li>
            ))}
          </ul>
        </details>
      ) : null}
      {report.errors.length > 0 ? (
        <details open>
          <summary className="cursor-pointer text-xs text-muted-foreground">
            {report.errors.length} 道题没导入成功
          </summary>
          <ul className="mt-1 flex flex-col gap-0.5 text-xs">
            {report.errors.map((item, index) => (
              <li key={`${item.question}-${index}`}>
                · {item.question} —— {item.reason}
              </li>
            ))}
          </ul>
        </details>
      ) : null}
    </div>
  );
}

function Select({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: string;
  options: { value: string; label: string }[];
  onChange: (value: string) => void;
}) {
  return (
    <label className="flex items-center gap-2 text-sm">
      <span className="w-12 shrink-0 text-muted-foreground">{label}</span>
      <select
        className="h-9 w-full rounded-md border bg-transparent px-2 text-sm"
        value={value}
        onChange={(event) => onChange(event.target.value)}
      >
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
    </label>
  );
}

/** 单题：收起时一行摘要，展开后看答案/关键点/来源，并可切到编辑态。 */
function PrivateRow({
  item,
  open,
  onToggle,
  onSave,
}: {
  item: PrivateQuestion;
  open: boolean;
  onToggle: () => void;
  onSave: (patch: PrivateQuestionPatch) => Promise<void>;
}) {
  const [editing, setEditing] = useState(false);
  const archived = item.status === "draft";

  useEffect(() => {
    if (!open) setEditing(false); // 收起即放弃编辑态，不留半截改动
  }, [open]);

  return (
    <li className="py-3">
      <button
        type="button"
        className="flex w-full flex-col gap-2 text-left"
        aria-expanded={open}
        onClick={onToggle}
      >
        <span className={`text-sm font-medium leading-relaxed ${archived ? "text-muted-foreground" : ""}`}>
          {item.question}
        </span>
        <span className="flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
          <Badge variant={archived ? "outline" : "secondary"}>{privateStatusLabel(item.status)}</Badge>
          <Badge variant="outline">{domainLabel(item.domain)}</Badge>
          <span>{difficultyLabel(item.difficulty)}</span>
          <span>· {item.topic}</span>
        </span>
      </button>

      {open ? (
        editing ? (
          <EditForm
            item={item}
            onCancel={() => setEditing(false)}
            onSubmit={async (patch) => {
              await onSave(patch);
              setEditing(false);
            }}
          />
        ) : (
          <div className="mt-3 flex flex-col gap-3 rounded-lg border bg-muted/30 p-3">
            <section className="flex flex-col gap-1">
              <h3 className="text-xs font-medium text-muted-foreground">参考答案</h3>
              <p className="whitespace-pre-wrap text-sm leading-relaxed">{item.answer}</p>
            </section>
            {item.key_points.length > 0 ? (
              <section className="flex flex-col gap-1">
                <h3 className="text-xs font-medium text-muted-foreground">关键点</h3>
                <ul className="flex flex-wrap gap-1.5">
                  {item.key_points.map((point) => (
                    <li key={point} className="rounded-md bg-background px-2 py-0.5 text-xs">
                      {point}
                    </li>
                  ))}
                </ul>
              </section>
            ) : null}
            {item.follow_ups.length > 0 ? (
              <section className="flex flex-col gap-1">
                <h3 className="text-xs font-medium text-muted-foreground">预设追问</h3>
                <ul className="flex flex-col gap-0.5 text-sm">
                  {item.follow_ups.map((follow) => (
                    <li key={follow}>· {follow}</li>
                  ))}
                </ul>
              </section>
            ) : null}
            {item.sources[0]?.source_detail ? (
              <p className="text-xs text-muted-foreground">来源：{item.sources[0].source_detail}</p>
            ) : null}

            <div className="flex gap-2">
              <Button variant="outline" size="sm" onClick={() => setEditing(true)}>
                编辑
              </Button>
              <Button
                variant="outline"
                size="sm"
                onClick={() => void onSave({ status: archived ? "enabled" : "draft" })}
              >
                {archived ? "恢复使用" : "归档"}
              </Button>
            </div>
            <p className="text-xs text-muted-foreground">
              {archived ? "已归档的题不参与出题，恢复后重新进入出题池。" : "归档后不再参与出题，随时可恢复。"}
            </p>
          </div>
        )
      ) : null}
    </li>
  );
}

function EditForm({
  item,
  onSubmit,
  onCancel,
}: {
  item: PrivateQuestion;
  onSubmit: (patch: PrivateQuestionPatch) => Promise<void>;
  onCancel: () => void;
}) {
  const [question, setQuestion] = useState(item.question);
  const [answer, setAnswer] = useState(item.answer);
  const [keyPoints, setKeyPoints] = useState(itemsToLines(item.key_points));
  const [followUps, setFollowUps] = useState(itemsToLines(item.follow_ups));
  const [topic, setTopic] = useState(item.topic);
  const [domain, setDomain] = useState(item.domain);
  const [difficulty, setDifficulty] = useState(item.difficulty);
  const [saving, setSaving] = useState(false);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setSaving(true);
    try {
      await onSubmit({
        question,
        answer,
        key_points: linesToItems(keyPoints),
        follow_ups: linesToItems(followUps),
        topic,
        domain,
        difficulty,
      });
    } finally {
      setSaving(false);
    }
  }

  return (
    <form
      className="mt-3 flex flex-col gap-3 rounded-lg border bg-muted/30 p-3"
      onSubmit={submit}
    >
      <Field label="题干">
        <Textarea value={question} onChange={(e) => setQuestion(e.target.value)} required />
      </Field>
      <Field label="参考答案">
        <Textarea
          value={answer}
          onChange={(e) => setAnswer(e.target.value)}
          className="min-h-24"
          required
        />
      </Field>
      <Field label="关键点" hint="一行一条（分号也可）">
        <Textarea value={keyPoints} onChange={(e) => setKeyPoints(e.target.value)} />
      </Field>
      <Field label="预设追问" hint="一行一条（分号也可）">
        <Textarea value={followUps} onChange={(e) => setFollowUps(e.target.value)} />
      </Field>
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
        <Field label="主题">
          <input
            className="h-9 w-full rounded-md border bg-transparent px-2 text-sm"
            value={topic}
            onChange={(e) => setTopic(e.target.value)}
            maxLength={100}
          />
        </Field>
        <Field label="知识域">
          <select
            className="h-9 w-full rounded-md border bg-transparent px-2 text-sm"
            value={domain}
            onChange={(e) => setDomain(e.target.value)}
          >
            {ENABLED_DOMAINS.map((value) => (
              <option key={value} value={value}>
                {domainLabel(value)}
              </option>
            ))}
          </select>
        </Field>
        <Field label="难度">
          <select
            className="h-9 w-full rounded-md border bg-transparent px-2 text-sm"
            value={difficulty}
            onChange={(e) => setDifficulty(e.target.value)}
          >
            {BANK_DIFFICULTIES.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </Field>
      </div>
      <div className="flex gap-2">
        <Button type="submit" size="sm" disabled={saving}>
          {saving ? "保存中…" : "保存"}
        </Button>
        <Button type="button" variant="outline" size="sm" onClick={onCancel} disabled={saving}>
          取消
        </Button>
      </div>
    </form>
  );
}

function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <label className="flex flex-col gap-1 text-sm">
      <span className="text-xs text-muted-foreground">
        {label}
        {hint ? <span className="ml-1">（{hint}）</span> : null}
      </span>
      {children}
    </label>
  );
}
