"use client";

import Link from "next/link";

/**
 * Displayed when a user tries to access a page or feature they don't
 * have permission to use.
 */
export function AccessDenied({
  message = "You don't have permission to access this page.",
  showDashboardLink = true,
}: {
  message?: string;
  showDashboardLink?: boolean;
}) {
  return (
    <div className="flex flex-col items-center justify-center min-h-[60vh] px-4">
      <div className="text-6xl mb-4">🔒</div>
      <h1 className="text-2xl font-bold text-gray-200 mb-2">Access Denied</h1>
      <p className="text-gray-400 text-center max-w-md mb-6">{message}</p>
      {showDashboardLink && (
        <Link
          href="/dashboard"
          className="px-4 py-2 bg-primary/20 text-primary rounded-lg hover:bg-primary/30 transition-colors"
        >
          Go to Dashboard
        </Link>
      )}
    </div>
  );
}
