import type { Metadata } from "next";
import Link from "next/link";

export const metadata: Metadata = {
  title: "404 | Page not found — Grid",
  robots: { index: false, follow: false },
};

export default function NotFound() {
  return (
    <main
      style={{
        minHeight: "100vh",
        display: "grid",
        placeItems: "center",
        padding: 22,
        backgroundColor: "#080c18",
        color: "#e5e7eb",
        fontFamily:
          '"Segoe UI", "Inter", "Helvetica Neue", Arial, sans-serif',
      }}
    >
      <section
        style={{
          width: "min(520px, 100%)",
          border: "1px solid #1a2438",
          borderRadius: 14,
          background: "#0d1322",
          overflow: "hidden",
          textAlign: "center",
        }}
      >
        <div
          style={{
            height: 2,
            background:
              "linear-gradient(90deg, transparent, #10b981, transparent)",
          }}
        />
        <div style={{ padding: "30px 32px" }}>
          <div
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              gap: 10,
            }}
          >
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src="/images/logo.svg" alt="Grid" width={26} height={26} />
            <span style={{ fontWeight: 800, fontSize: 17, color: "#fff" }}>
              Grid
            </span>
          </div>
          <div
            style={{
              fontFamily:
                "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace",
              fontSize: 88,
              fontWeight: 800,
              lineHeight: 1.1,
              color: "#34d399",
              userSelect: "none",
            }}
          >
            404
          </div>
          <h1
            style={{
              margin: "8px 0",
              fontSize: 22,
              color: "#fff",
              fontWeight: 800,
            }}
          >
            This page doesn&apos;t exist
          </h1>
          <p style={{ color: "#9ca3af", fontSize: 14, lineHeight: 1.55 }}>
            The dashboard page you asked for was moved or never existed.
          </p>
          <div style={{ marginTop: 20 }}>
            <Link
              href="/"
              style={{
                display: "inline-block",
                border: "1px solid #1a2438",
                borderRadius: 8,
                padding: "11px 18px",
                fontSize: 13,
                fontWeight: 700,
                color: "#e5e7eb",
                background: "#111827",
                textDecoration: "none",
              }}
            >
              Back to dashboard
            </Link>
          </div>
          <p style={{ marginTop: 16, fontSize: 12, color: "#6b7280" }}>
            Grid edge &middot; HTTP 404
          </p>
        </div>
      </section>
    </main>
  );
}
