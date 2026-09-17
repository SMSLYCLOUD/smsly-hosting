"use client";

// Root failure surface: runs WITHOUT the app layout (no providers, no
// globals.css), so every style here is inline and every asset reference
// is avoided. Never import dashboard components from this file.
export default function GlobalError({
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <html lang="en">
      <head>
        <meta name="robots" content="noindex, nofollow" />
        <title>Dashboard unavailable — Grid</title>
      </head>
      <body
        style={{
          margin: 0,
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
        <main
          style={{
            width: "min(520px, 100%)",
            border: "1px solid #1a2438",
            borderRadius: 14,
            background: "#0d1322",
            padding: "34px 32px",
            textAlign: "center",
          }}
        >
          <div style={{ fontWeight: 800, fontSize: 18, color: "#fff" }}>
            Grid
          </div>
          <div style={{ fontSize: 11, color: "#6b7280", marginTop: 2 }}>
            by Trulay
          </div>
          <h1
            style={{
              margin: "18px 0 8px",
              fontSize: 22,
              color: "#fff",
              fontWeight: 800,
            }}
          >
            Dashboard unavailable
          </h1>
          <p style={{ color: "#9ca3af", fontSize: 14, lineHeight: 1.55 }}>
            The dashboard shell failed to load. Your services keep running
            — this is a display problem only.
          </p>
          <button
            type="button"
            onClick={() => reset()}
            style={{
              marginTop: 20,
              border: "1px solid #10b981",
              borderRadius: 8,
              padding: "11px 18px",
              fontSize: 13,
              fontWeight: 700,
              color: "#fff",
              background: "rgba(16, 185, 129, 0.15)",
              cursor: "pointer",
            }}
          >
            Reload dashboard
          </button>
        </main>
      </body>
    </html>
  );
}
