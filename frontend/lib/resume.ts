/**
 * 简历纯逻辑（P2-M11 FR-28）：来源判据 / 解析结果摘要 / 如实交代文案。
 *
 * 浏览器专属的部分（FormData 上传）在 `lib/api.ts` 的 `uploadResume`；
 * 这里只放可测的判据，参数与后端 `backend/app/tools/resumes.py` 对齐。
 */

export const RESUME_ACCEPT = ".pdf,.md,.txt,.markdown";
export const RESUME_MAX_MB = 2; // 与后端 resumes.MAX_RESUME_BYTES 同值
export const RESUME_SUFFIX_HINT = "支持 .pdf / .md / .txt，≤2MB";

/**
 * 如实交代（用户口径：涉及个人数据的处理不许用户猜）：
 * 简历原文会发给模型解析、只用于本场出题与评分；不解析也能开始面试。
 */
export const RESUME_DISCLOSURE =
  "简历会发送给模型解析，仅用于本场出题与评分；不解析也可以开始面试。";

export const RESUME_TITLE = "简历（可选，用于把项目深挖题与追问变得具体）";
export const RESUME_PASTE_PLACEHOLDER =
  "或直接粘贴简历文本（扫描版 / 图片版 PDF 读不出文字时用这个）";

export type ResumeSource = "file" | "text" | null;

/** 解析用哪一份：优先文件（选了文件说明意图明确），其次粘贴文本，都没有 → null。 */
export function resumeSource(file: Pick<File, "name"> | null, text: string): ResumeSource {
  if (file) return "file";
  if (text.trim()) return "text";
  return null;
}

export type ResumeParseResult = {
  resume_id: string;
  filename: string;
  projects: string[];
  skills: string[];
  chars: number;
};

/**
 * 解析结果摘要（**不含原文**）：让「解析错」当场看得见——读到 0 段项目经历时
 * 用户立刻知道该改传粘贴文本，而不是等到面试官出题才发现「它没读懂我的简历」。
 */
export function resumeDigest(result: Pick<ResumeParseResult, "projects" | "skills" | "chars">): string {
  const parts = [`已读到 ${result.projects.length} 段项目经历`];
  if (result.skills.length) parts.push(`${result.skills.length} 项技能`);
  parts.push(`约 ${result.chars} 字`);
  return parts.join(" · ");
}
