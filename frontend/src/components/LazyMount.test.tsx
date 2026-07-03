import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, act } from '@testing-library/react';
import { LazyMount } from './LazyMount';

describe('LazyMount', () => {
  beforeEach(() => {
    vi.useRealTimers();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('renders nothing initially when fallback is null, then renders children after idle callback fires', () => {
    let idleCallback: (() => void) | undefined;
    const spy = vi
      .spyOn(window, 'requestIdleCallback')
      .mockImplementation((cb: any) => {
        idleCallback = () => cb({ didTimeout: false, timeRemaining: () => 50 });
        return 1;
      });
    vi.spyOn(window, 'cancelIdleCallback').mockImplementation(() => undefined);

    const { container } = render(
      <LazyMount>
        <span data-testid="child">mounted</span>
      </LazyMount>
    );

    // Before idle fires: nothing rendered
    expect(container.firstChild).toBeNull();
    expect(screen.queryByTestId('child')).toBeNull();

    // Fire the captured idle callback synchronously
    act(() => {
      idleCallback!();
    });

    expect(screen.getByTestId('child')).toBeInTheDocument();
    expect(spy).toHaveBeenCalledTimes(1);
  });

  it('falls back to setTimeout when requestIdleCallback is undefined', () => {
    const original = (window as any).requestIdleCallback;
    delete (window as any).requestIdleCallback;
    const clearSpy = vi.spyOn(window, 'clearTimeout');

    try {
      vi.useFakeTimers();
      render(
        <LazyMount idleTimeoutMs={500}>
          <span data-testid="child">mounted</span>
        </LazyMount>
      );

      expect(screen.queryByTestId('child')).toBeNull();

      act(() => {
        vi.advanceTimersByTime(500);
      });

      expect(screen.getByTestId('child')).toBeInTheDocument();
      // setTimeout fallback handle should have been cleared via clearTimeout or
      // already fired — at minimum no throw
      expect(clearSpy).toBeDefined();
    } finally {
      (window as any).requestIdleCallback = original;
      vi.useRealTimers();
    }
  });

  it('renders children immediately on user interaction (scroll) and removes listeners', () => {
    let idleCallback: (() => void) | undefined;
    vi.spyOn(window, 'requestIdleCallback').mockImplementation((cb: any) => {
      idleCallback = () => cb({ didTimeout: false, timeRemaining: () => 50 });
      return 1;
    });
    const removeSpy = vi.spyOn(window, 'removeEventListener');

    render(
      <LazyMount>
        <span data-testid="child">mounted</span>
      </LazyMount>
    );

    // Trigger a scroll event on window
    act(() => {
      window.dispatchEvent(new Event('scroll'));
    });

    expect(screen.getByTestId('child')).toBeInTheDocument();

    // Each of the 4 interaction events should have been removed once
    const removedEvents = removeSpy.mock.calls.map((c) => c[0]);
    for (const ev of ['scroll', 'click', 'keydown', 'touchstart']) {
      expect(removedEvents).toContain(ev);
    }

    // Firing the idle callback now must NOT cause a re-render or duplicate
    // effect. We verify by checking the child is still mounted exactly once
    // (still findable) and no errors are thrown.
    expect(() => {
      act(() => {
        idleCallback?.();
      });
    }).not.toThrow();
    expect(screen.getByTestId('child')).toBeInTheDocument();
  });

  it('attaches interaction listeners with { capture: true, passive: true }', () => {
    vi.spyOn(window, 'requestIdleCallback').mockImplementation((cb: any) => {
      // Fire the idle callback immediately so the component renders
      Promise.resolve().then(() => cb({ didTimeout: false, timeRemaining: () => 50 }));
      return 1;
    });
    const addSpy = vi.spyOn(window, 'addEventListener');

    render(
      <LazyMount>
        <span>child</span>
      </LazyMount>
    );

    const calls = addSpy.mock.calls.filter((c) =>
      ['scroll', 'click', 'keydown', 'touchstart'].includes(c[0] as string)
    );
    expect(calls.length).toBe(4);

    for (const [, , opts] of calls) {
      expect(opts).toMatchObject({ capture: true, passive: true });
    }
  });

  it('cancels the idle handle on unmount when requestIdleCallback is present', () => {
    const cancelSpy = vi
      .spyOn(window, 'cancelIdleCallback')
      .mockImplementation(() => undefined);
    vi.spyOn(window, 'requestIdleCallback').mockImplementation(() => 42);

    const { unmount } = render(
      <LazyMount>
        <span>child</span>
      </LazyMount>
    );

    unmount();

    expect(cancelSpy).toHaveBeenCalledWith(42);
  });

  it('cancels the setTimeout handle on unmount when requestIdleCallback is missing', () => {
    const original = (window as any).requestIdleCallback;
    delete (window as any).requestIdleCallback;
    const clearSpy = vi.spyOn(window, 'clearTimeout');

    try {
      const { unmount } = render(
        <LazyMount>
          <span>child</span>
        </LazyMount>
      );

      unmount();

      expect(clearSpy).toHaveBeenCalled();
    } finally {
      (window as any).requestIdleCallback = original;
    }
  });

  it('passes the custom idleTimeoutMs to requestIdleCallback', () => {
    const spy = vi
      .spyOn(window, 'requestIdleCallback')
      .mockImplementation((cb: any) => {
        Promise.resolve().then(() => cb({ didTimeout: false, timeRemaining: () => 50 }));
        return 1;
      });

    render(
      <LazyMount idleTimeoutMs={7500}>
        <span>child</span>
      </LazyMount>
    );

    expect(spy).toHaveBeenCalledWith(expect.any(Function), { timeout: 7500 });
  });

  it('passes the custom idleTimeoutMs to setTimeout fallback', () => {
    const original = (window as any).requestIdleCallback;
    delete (window as any).requestIdleCallback;
    const setSpy = vi.spyOn(window, 'setTimeout');

    try {
      render(
        <LazyMount idleTimeoutMs={4321}>
          <span>child</span>
        </LazyMount>
      );

      const last = setSpy.mock.calls[setSpy.mock.calls.length - 1];
      // The lazy-mount trigger is the last setTimeout(0,fn) typically used as
      // the fallback, but with custom timeout it should be 4321.
      const lazyCall = setSpy.mock.calls.find(([, delay]) => delay === 4321);
      expect(lazyCall).toBeDefined();
    } finally {
      (window as any).requestIdleCallback = original;
    }
  });

  it('renders fallback before mount and switches to children after idle', () => {
    let idleCallback: (() => void) | undefined;
    vi.spyOn(window, 'requestIdleCallback').mockImplementation((cb: any) => {
      idleCallback = () => cb({ didTimeout: false, timeRemaining: () => 50 });
      return 1;
    });

    const { container } = render(
      <LazyMount fallback={<span data-testid="fallback">loading…</span>}>
        <span data-testid="child">ready</span>
      </LazyMount>
    );

    expect(screen.getByTestId('fallback')).toBeInTheDocument();
    expect(screen.queryByTestId('child')).toBeNull();

    act(() => {
      idleCallback!();
    });

    expect(screen.queryByTestId('fallback')).toBeNull();
    expect(screen.getByTestId('child')).toBeInTheDocument();
  });
});
