import type { NextConfig } from "next";

/** 后端地址：T7 生产由 nginx 同构转发，dev 走 Next rewrites（SPEC §7）。 */
const backend = process.env.BACKEND_ORIGIN ?? "http://127.0.0.1:8000";
/**
 * P5 网关地址：dev 下 /api/v1/learning/** 走 Java 网关（JWT 校验 / 限流 / 路由），
 * 其余 /api/* 仍直连 Python。compose 里浏览器直接走 nginx，不经过 Next rewrite。
 */
const gateway = process.env.GATEWAY_ORIGIN ?? "http://127.0.0.1:8080";

const nextConfig: NextConfig = {
  output: "standalone", // Docker 运行层只带产物（见 frontend/Dockerfile）
  devIndicators: false, // 开发态浮层（英文，不可本地化），截图/录 demo 时碍事
  async rewrites() {
    return [
      // 具体路径必须在 /api/:path* 之前，否则学习 API 会被 Python 的重写抢走。
      {
        source: "/api/v1/learning/:path*",
        destination: `${gateway}/api/v1/learning/:path*`,
      },
      { source: "/api/:path*", destination: `${backend}/api/:path*` },
    ];
  },
};

export default nextConfig;
