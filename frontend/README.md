# Marda 前端

Marda 码达的前端（Next.js 15 App Router + TypeScript + Tailwind + shadcn/ui + Recharts）。

安装、启动、测试与部署说明都在仓库根目录的 [README](../README.md)；页面清单、路由守卫、SSE 与各类前端口径见
[docs/SPEC.md](../docs/SPEC.md) §7/§9。

```bash
pnpm install && pnpm dev    # http://localhost:3000（需后端在 8000，见根 README「开发模式」）
pnpm test                   # vitest：lib 纯逻辑
pnpm lint && pnpm build
```
