/**
 * 把 Monaco 的 AMD 构建产物复制到 public/monaco/vs（M15）。
 *
 * 为什么需要：`@monaco-editor/react` 默认从 jsdelivr CDN 取 `vs/loader.js`，
 * 国内网络/离线容器拿不到，也与我们「同源静态资源」的口径不符。把
 * `node_modules/monaco-editor/min/vs` 复制到本站目录后，在编辑器模块里
 * `loader.config({ paths: { vs: "/monaco/vs" } })` 即可完全离线。
 *
 * **不要改成只复制 editor.main.js**：语言语法、worker、NLS 都是按需加载的
 * 兄弟文件（0.57 起还带哈希分片），整目录复制才是可用的运行时。
 *
 * 产物是构建中间物，已在 frontend/.gitignore 里忽略；每次 dev/build 前重跑。
 * 用 .version 戳跳过重复复制（整目录几十 MB，每次 dev 都拷太慢）。
 */
import { cpSync, existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const packageDir = resolve(root, "node_modules/monaco-editor");
const source = resolve(packageDir, "min/vs");
const targetRoot = resolve(root, "public/monaco");
const target = resolve(targetRoot, "vs");
const stamp = resolve(targetRoot, ".version");

if (!existsSync(source)) {
  console.error(
    `[monaco] 找不到 AMD 构建目录：${source}\n先执行 pnpm install，再重跑 dev/build。`,
  );
  process.exit(1);
}

const version = JSON.parse(readFileSync(resolve(packageDir, "package.json"), "utf8")).version;
const current = existsSync(stamp) ? readFileSync(stamp, "utf8").trim() : "";
if (current === version && existsSync(resolve(target, "loader.js"))) {
  console.log(`[monaco] ${version} 已是当前版本，跳过复制`);
  process.exit(0);
}

rmSync(targetRoot, { recursive: true, force: true });
mkdirSync(targetRoot, { recursive: true });
cpSync(source, target, { recursive: true });
writeFileSync(stamp, `${version}\n`);
console.log(`[monaco] ${version} 已复制到 ${target}`);
