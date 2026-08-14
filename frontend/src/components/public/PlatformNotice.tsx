'use client';

import Link from 'next/link';
import { Home, ShieldAlert, Zap } from 'lucide-react';

interface PlatformNoticeProps {
  badge?: string;
  title: string;
  message: string;
  secondaryMessage?: string;
  showRetry?: boolean;
  onRetry?: () => void;
}

export default function PlatformNotice({
  badge = 'System Notice',
  title,
  message,
  secondaryMessage,
  showRetry = false,
  onRetry,
}: PlatformNoticeProps) {
  return (
    <main className="notice-root">
      <section className="notice-card">
        <div className="notice-grid">
          <div className="notice-text">
            <div className="notice-pill">
              <span>{badge}</span>
            </div>
            <h1>{title}</h1>
            <p>{message}</p>
          </div>
          <div className="notice-icon">
            <ShieldAlert size={44} />
          </div>
        </div>

        {secondaryMessage && (
          <div className="notice-secondary">
            <Zap size={18} />
            <p>{secondaryMessage}</p>
          </div>
        )}

        <div className="notice-actions">
          {showRetry && onRetry && (
            <button className="notice-btn notice-primary" onClick={onRetry}>
              Retry Connection
            </button>
          )}

          <Link href="/" className="notice-btn notice-outline">
            <Home size={18} style={{ marginRight: 8 }} />
            Go Back Home
          </Link>
        </div>

        <div className="notice-links">
          <Link href="/status" className="notice-link">
            Status
          </Link>
          <Link href="/docs" className="notice-link">
            Documentation
          </Link>
          <Link href="/contact" className="notice-link">
            Support
          </Link>
        </div>
      </section>

      {/* Inline critical styles so the notice stays fully styled even if /_next/static is blocked */}
      <style jsx>{`
        :global(body) {
          margin: 0;
          background: #080c18;
          color: #e5e7eb;
          font-family: 'Inter', system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
        }
        .notice-root {
          min-height: 100vh;
          display: flex;
          align-items: center;
          justify-content: center;
          padding: 32px;
          background-color: #080c18;
          background-image:
            linear-gradient(rgba(255, 255, 255, 0.025) 1px, transparent 1px),
            linear-gradient(90deg, rgba(255, 255, 255, 0.025) 1px, transparent 1px),
            linear-gradient(rgba(255, 255, 255, 0.05) 1px, transparent 1px),
            linear-gradient(90deg, rgba(255, 255, 255, 0.05) 1px, transparent 1px);
          background-size: 24px 24px, 24px 24px, 96px 96px, 96px 96px;
        }
        .notice-card {
          width: min(960px, 100%);
          background: #0d1322;
          border: 1px solid #1a2438;
          border-radius: 12px;
          padding: 32px;
        }
        .notice-grid {
          display: grid;
          grid-template-columns: 1fr auto;
          gap: 24px;
          align-items: center;
        }
        .notice-text h1 {
          margin: 12px 0 8px;
          font-size: clamp(28px, 4vw, 42px);
          font-weight: 800;
          color: #fff;
          line-height: 1.1;
        }
        .notice-text p {
          margin: 0;
          color: #9ca3af;
          font-size: 16px;
          line-height: 1.6;
        }
        .notice-pill {
          display: inline-flex;
          align-items: center;
          gap: 8px;
          padding: 6px 12px;
          border-radius: 6px;
          border: 1px solid #1a2438;
          background: #111827;
          letter-spacing: 0.18em;
          font-size: 10px;
          text-transform: uppercase;
          color: #10b981;
          font-weight: 700;
        }
        .notice-icon {
          width: 96px;
          height: 96px;
          border-radius: 12px;
          display: grid;
          place-items: center;
          background: #111827;
          border: 1px solid #1a2438;
          color: #f59e0b;
        }
        .notice-secondary {
          margin-top: 18px;
          display: flex;
          gap: 12px;
          align-items: flex-start;
          padding: 14px 16px;
          border-radius: 8px;
          border: 1px solid #1a2438;
          background: #111827;
          color: #9ca3af;
          font-size: 15px;
          line-height: 1.5;
        }
        .notice-actions {
          margin-top: 24px;
          display: grid;
          grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
          gap: 12px;
        }
        .notice-btn {
          height: 48px;
          border-radius: 8px;
          border: 1px solid #1a2438;
          background: #111827;
          color: #e5e7eb;
          font-weight: 700;
          letter-spacing: 0.01em;
          display: inline-flex;
          align-items: center;
          justify-content: center;
          gap: 6px;
          text-decoration: none;
          cursor: pointer;
          transition: border-color 0.2s ease, background 0.2s ease;
        }
        .notice-btn:hover {
          border-color: #2a3a58;
          background: #162032;
        }
        .notice-primary {
          background: #10b981;
          border: 1px solid #10b981;
          color: #05060d;
        }
        .notice-primary:hover {
          background: #059669;
          border-color: #059669;
        }
        .notice-outline {
          border-color: #1a2438;
        }
        .notice-links {
          margin-top: 22px;
          padding-top: 16px;
          border-top: 1px solid #1a2438;
          display: flex;
          flex-wrap: wrap;
          gap: 18px;
          font-size: 11px;
          letter-spacing: 0.16em;
          text-transform: uppercase;
          color: #6b7280;
          font-weight: 700;
        }
        .notice-link {
          color: inherit;
          text-decoration: none;
        }
        .notice-link:hover {
          color: #10b981;
        }
        @media (max-width: 720px) {
          .notice-card {
            padding: 24px;
          }
          .notice-grid {
            grid-template-columns: 1fr;
          }
          .notice-icon {
            margin-top: -6px;
            justify-self: flex-start;
          }
        }
      `}</style>
    </main>
  );
}
