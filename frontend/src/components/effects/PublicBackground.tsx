"use client";

/**
 * PublicBackground — Lightweight CSS-only background for public pages.
 *
 * No canvas, no JS animation loop. Uses:
 * - Subtle emerald radial glow from top
 * - Static minor/major grid lines
 * - Dot pattern at intersections
 *
 * Performant for high-traffic landing pages where OperationalMesh
 * canvas animation would be unnecessary overhead.
 */

const PUBLIC_ROUTES = [
  "/",
  "/pricing",
  "/compare",
  "/contact",
  "/docs",
  "/legal",
  "/status",
  "/notice",
  "/get-started",
  "/logout",
  "/forgot-password",
];

export function isPublicRoute(pathname: string): boolean {
  if (!pathname) return true;
  if (pathname === "/") return true;
  return PUBLIC_ROUTES.some(
    (route) => pathname === route || pathname.startsWith(route + "/"),
  );
}

export function PublicBackground() {
  return (
    <div
      className="fixed inset-0 pointer-events-none"
      style={{ zIndex: -1 }}
      aria-hidden="true"
    >
      <style jsx>{`
        div {
          background-color: #080c18;
          background-image:
            radial-gradient(circle at 50% 0%, rgba(16, 185, 129, 0.06), transparent 50%),
            radial-gradient(circle at 80% 80%, rgba(59, 130, 246, 0.04), transparent 45%),
            linear-gradient(rgba(255, 255, 255, 0.03) 1px, transparent 1px),
            linear-gradient(90deg, rgba(255, 255, 255, 0.03) 1px, transparent 1px),
            linear-gradient(rgba(255, 255, 255, 0.06) 1px, transparent 1px),
            linear-gradient(90deg, rgba(255, 255, 255, 0.06) 1px, transparent 1px),
            radial-gradient(circle, rgba(100, 116, 139, 0.15) 1px, transparent 1.5px);
          background-size:
            100% 100%,
            100% 100%,
            32px 32px,
            32px 32px,
            128px 128px,
            128px 128px,
            32px 32px;
          background-position:
            0 0,
            0 0,
            -1px -1px,
            -1px -1px,
            -1px -1px,
            -1px -1px,
            0 0;
        }
      `}</style>
    </div>
  );
}
