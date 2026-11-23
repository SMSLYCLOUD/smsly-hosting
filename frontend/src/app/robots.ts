import type { MetadataRoute } from "next";

function getSiteOrigin(): string {
  const raw = (process.env.NEXT_PUBLIC_APP_URL || "https://grid.trulay.co").trim();
  try {
    return new URL(raw).origin;
  } catch {
    return "https://grid.trulay.co";
  }
}

export default function robots(): MetadataRoute.Robots {
  const origin = getSiteOrigin();
  return {
    rules: [
      {
        userAgent: "*",
        allow: "/",
        disallow: [
          "/dashboard",
          "/admin-dashboard",
          "/billing",
          "/projects",
          "/services",
          "/deployments",
          "/servers",
          "/settings",
          "/marketplace",
          "/ecosystem",
          "/intelligence",
          "/backups",
          "/transfers",
          "/functions",
          "/activity",
          "/autoscaler",
          "/tunnels",
          "/network",
          "/clusters",
          "/replication",
          "/mcp",
          "/new",
          "/client",
          "/api/",
        ],
      },
    ],
    sitemap: `${origin}/sitemap.xml`,
  };
}
