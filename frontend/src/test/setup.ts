/**
 * Vitest setup file — runs once before any test file.
 *
 * Pulls in @testing-library/jest-dom matchers (toBeInTheDocument, etc.)
 * and polyfills the few browser APIs that jsdom doesn't ship with but
 * the app code (and its tests) rely on.
 */
import '@testing-library/jest-dom/vitest';
import { cleanup } from '@testing-library/react';
import { afterEach, vi } from 'vitest';

// Auto-cleanup the rendered DOM after each test so suites don't leak.
afterEach(() => {
  cleanup();
});

// jsdom doesn't implement matchMedia or IntersectionObserver; the
// Starfield, LazyMount, and other media-query-driven components would
// crash on import otherwise. Stub both with no-op defaults.
if (typeof window !== 'undefined') {
  if (!window.matchMedia) {
    Object.defineProperty(window, 'matchMedia', {
      writable: true,
      value: vi.fn().mockImplementation((query: string) => ({
        matches: false,
        media: query,
        onchange: null,
        addListener: vi.fn(),
        removeListener: vi.fn(),
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
        dispatchEvent: vi.fn(),
      })),
    });
  }

  if (!window.IntersectionObserver) {
    class MockIntersectionObserver {
      observe = vi.fn();
      unobserve = vi.fn();
      disconnect = vi.fn();
      takeRecords = vi.fn(() => []);
      root = null;
      rootMargin = '';
      thresholds = [];
    }
    (window as unknown as { IntersectionObserver: typeof MockIntersectionObserver }).IntersectionObserver =
      MockIntersectionObserver;
  }

  if (!window.ResizeObserver) {
    class MockResizeObserver {
      observe = vi.fn();
      unobserve = vi.fn();
      disconnect = vi.fn();
    }
    (window as unknown as { ResizeObserver: typeof MockResizeObserver }).ResizeObserver =
      MockResizeObserver;
  }

  if (!window.requestIdleCallback) {
    (window as any).requestIdleCallback = vi.fn((cb: any) => {
      return setTimeout(() => cb({ didTimeout: false, timeRemaining: () => 50 }), 0);
    });
  }

  if (!window.cancelIdleCallback) {
    (window as any).cancelIdleCallback = vi.fn((id: any) => {
      clearTimeout(id);
    });
  }
}

// next/navigation mock — the App Router hooks are referenced by
// AuthProvider and other client providers. Default to a happy-path stub
// that individual tests can override with vi.mock('next/navigation').
vi.mock('next/navigation', () => ({
  useRouter: () => ({
    push: vi.fn(),
    replace: vi.fn(),
    back: vi.fn(),
    forward: vi.fn(),
    refresh: vi.fn(),
    prefetch: vi.fn(),
  }),
  usePathname: () => '/',
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({}),
}));
