'use client';

import Link from 'next/link';
import { Home, ShieldAlert, Sparkles, Zap } from 'lucide-react';

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
              <Sparkles size={16} />
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
          background: #05060d;
          color: #e5e7eb;
          font-family: 'Inter', system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
        }
        .notice-root {
          min-height: 100vh;
          display: flex;
          align-items: center;
          justify-content: center;
          padding: 32px;
          background: radial-gradient(circle at 20% 20%, rgba(59, 130, 246, 0.12), transparent 35%),
            radial-gradient(circle at 80% 10%, rgba(45, 212, 191, 0.1), transparent 30%),
            radial-gradient(circle at 70% 70%, rgba(168, 85, 247, 0.08), transparent 32%),
            radial-gradient(circle at 15% 80%, rgba(79, 70, 229, 0.12), transparent 28%),
            #05060d;
        }
        .notice-card {
          width: min(960px, 100%);
          background: rgba(255, 255, 255, 0.04);
          border: 1px solid rgba(255, 255, 255, 0.08);
          border-radius: 28px;
          padding: 32px;
          backdrop-filter: blur(12px);
          box-shadow: 0 20px 70px rgba(0, 0, 0, 0.35);
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
          color: rgba(229, 231, 235, 0.82);
          font-size: 16px;
          line-height: 1.6;
        }
        .notice-pill {
          display: inline-flex;
          align-items: center;
          gap: 8px;
          padding: 8px 12px;
          border-radius: 999px;
          border: 1px solid rgba(255, 255, 255, 0.12);
          background: rgba(255, 255, 255, 0.05);
          letter-spacing: 0.18em;
          font-size: 10px;
          text-transform: uppercase;
          color: #93c5fd;
          font-weight: 700;
        }
        .notice-icon {
          width: 96px;
          height: 96px;
          border-radius: 24px;
          display: grid;
          place-items: center;
          background: linear-gradient(135deg, rgba(251, 191, 36, 0.16), rgba(249, 115, 22, 0.12));
          border: 1px solid rgba(251, 191, 36, 0.3);
          color: #fcd34d;
          box-shadow: 0 10px 40px rgba(251, 191, 36, 0.22);
        }
        .notice-secondary {
          margin-top: 18px;
          display: flex;
          gap: 12px;
          align-items: flex-start;
          padding: 14px 16px;
          border-radius: 16px;
          border: 1px solid rgba(255, 255, 255, 0.08);
          background: rgba(255, 255, 255, 0.05);
          color: rgba(229, 231, 235, 0.82);
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
          border-radius: 12px;
          border: 1px solid rgba(255, 255, 255, 0.14);
          background: rgba(255, 255, 255, 0.06);
          color: #e5e7eb;
          font-weight: 700;
          letter-spacing: 0.01em;
          display: inline-flex;
          align-items: center;
          justify-content: center;
          gap: 6px;
          text-decoration: none;
          cursor: pointer;
          transition: transform 0.1s ease, border-color 0.2s ease, background 0.2s ease;
        }
        .notice-btn:hover {
          transform: translateY(-1px);
          border-color: rgba(255, 255, 255, 0.26);
          background: rgba(255, 255, 255, 0.1);
        }
        .notice-primary {
          background: linear-gradient(135deg, #2563eb, #4f46e5);
          border: none;
          color: #fff;
          box-shadow: 0 12px 30px rgba(37, 99, 235, 0.35);
        }
        .notice-outline {
          border-color: rgba(255, 255, 255, 0.22);
        }
        .notice-links {
          margin-top: 22px;
          padding-top: 16px;
          border-top: 1px solid rgba(255, 255, 255, 0.08);
          display: flex;
          flex-wrap: wrap;
          gap: 18px;
          font-size: 11px;
          letter-spacing: 0.16em;
          text-transform: uppercase;
          color: rgba(229, 231, 235, 0.6);
          font-weight: 700;
        }
        .notice-link {
          color: inherit;
          text-decoration: none;
        }
        .notice-link:hover {
          color: #93c5fd;
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
