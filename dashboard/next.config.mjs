/** Static export: nginx serves `out/` and proxies /api/* to FastAPI on the same origin. */
const nextConfig = {
  output: "export",
  images: { unoptimized: true },
  reactStrictMode: true,
};

export default nextConfig;
