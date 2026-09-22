import type { NextConfig } from "next";

/** 后端地址：T7 生产由 nginx 同构转发，dev 走 Next rewrites（SPEC §7）。 */
const backend = process.env.BACKEND_ORIGIN ?? "http://127.0.0.1:8000";

const nextConfig: NextConfig = {
  output: "standalone", // Docker 运行层只带产物（见 frontend/Dockerfile）
  devIndicators: false, // 开发态浮层（英文，不可本地化），截图/录 demo 时碍事
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${backend}/api/:path*` }];
  },
};

export default nextConfig;
