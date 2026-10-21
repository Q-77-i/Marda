/**
 * 把 Excalidraw 的字体复制到 public/excalidraw-assets（P2-M14）。
 *
 * 为什么需要：Excalidraw 运行时会按 `window.EXCALIDRAW_ASSET_PATH` 动态取字体；
 * 默认回退到 esm.run CDN，国内网络/离线容器可能拿不到。复制到本站资源目录后
 * 白板字体（Virgil/Excalifont/中文 Xiaolai 等）稳定可用。
 *
 * 产物是构建中间物，已在 frontend/.gitignore 里忽略；每次 dev/build 前重跑。
 */
import { cpSync, existsSync, mkdirSync, rmSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const source = resolve(root, "node_modules/@excalidraw/excalidraw/dist/prod/fonts");
const targetRoot = resolve(root, "public/excalidraw-assets");
const target = resolve(targetRoot, "fonts");

if (!existsSync(source)) {
  console.error(
    `[excalidraw] 找不到字体目录：${source}\n先执行 pnpm install，再重跑 dev/build。`,
  );
  process.exit(1);
}

rmSync(targetRoot, { recursive: true, force: true });
mkdirSync(dirname(target), { recursive: true });
cpSync(source, target, { recursive: true });
console.log(`[excalidraw] 字体已复制到 ${target}`);
