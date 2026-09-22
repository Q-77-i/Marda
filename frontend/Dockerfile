# 前端镜像：pnpm 装依赖 → next build（standalone）→ node 直接运行产物
FROM node:24-alpine AS base
ENV NEXT_TELEMETRY_DISABLED=1
RUN npm config set registry https://registry.npmmirror.com && npm i -g pnpm@12.4.2

# ---- 依赖层（锁文件，可缓存）----
FROM base AS deps
WORKDIR /app
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile

# ---- 构建层 ----
FROM base AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN pnpm build

# ---- 运行层（只带 standalone 产物）----
FROM base AS runner
WORKDIR /app
ENV NODE_ENV=production \
    PORT=3000 \
    HOSTNAME=0.0.0.0
COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static
COPY --from=builder /app/public ./public
EXPOSE 3000
CMD ["node", "server.js"]
