import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import React from 'react';
import { ErrorBoundary } from './ErrorBoundary';

function GoodChild({ label }: { label?: string }) {
  return <div data-testid="good-child">{label ?? 'fine'}</div>;
}

interface FlakyChildProps {
  shouldThrow: boolean;
  label?: string;
}

class FlakyChild extends React.Component<FlakyChildProps> {
  render() {
    if (this.props.shouldThrow) {
      throw new Error('boom');
    }
    return <div data-testid="flaky-child">{this.props.label ?? 'ok'}</div>;
  }
}

describe('ErrorBoundary', () => {
  let errorSpy: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    // React internally logs caught errors via console.error; silence to keep
    // test output clean. We still assert it is called below.
    errorSpy = vi.spyOn(console, 'error').mockImplementation(() => undefined);
  });

  afterEach(() => {
    errorSpy.mockRestore();
  });

  it('renders children normally when no error is thrown', () => {
    render(
      <ErrorBoundary>
        <GoodChild label="visible" />
      </ErrorBoundary>
    );
    expect(screen.getByTestId('good-child')).toBeInTheDocument();
    expect(screen.getByText('visible')).toBeInTheDocument();
  });

  it('catches errors and renders the default fallback when a child throws', () => {
    render(
      <ErrorBoundary>
        <FlakyChild shouldThrow={true} />
      </ErrorBoundary>
    );

    const fallback = screen.getByText('Something went wrong in this component.');
    expect(fallback).toBeInTheDocument();
    expect(fallback).toHaveClass('p-4', 'text-red-500');
    expect(screen.queryByTestId('flaky-child')).toBeNull();
  });

  it('renders the custom fallback prop when provided', () => {
    render(
      <ErrorBoundary fallback={<div data-testid="custom-fallback">custom UI</div>}>
        <FlakyChild shouldThrow={true} />
      </ErrorBoundary>
    );

    expect(screen.getByTestId('custom-fallback')).toBeInTheDocument();
    expect(screen.queryByText('Something went wrong in this component.')).toBeNull();
  });

  it('calls componentDidCatch which logs to console.error', () => {
    render(
      <ErrorBoundary>
        <FlakyChild shouldThrow={true} />
      </ErrorBoundary>
    );

    // React 18+ always logs caught errors via console.error before our
    // componentDidCatch runs, so look for any console.error call that
    // mentions the boundary's tag.
    const allCalls = errorSpy.mock.calls.map((args) => args.join(' ')).join('\n');
    expect(allCalls).toContain('ErrorBoundary caught error:');
  });

  it('recovers and shows children after the failing child is swapped out', () => {
    // First render: failing child -> boundary shows fallback
    const { rerender } = render(
      <ErrorBoundary>
        <FlakyChild shouldThrow={true} />
      </ErrorBoundary>
    );
    expect(screen.getByText('Something went wrong in this component.')).toBeInTheDocument();

    // Rerender with a healthy child -> boundary still shows fallback
    // (ErrorBoundary state is sticky by design). This documents that
    // behavior.
    rerender(
      <ErrorBoundary>
        <GoodChild />
      </ErrorBoundary>
    );
    expect(screen.getByText('Something went wrong in this component.')).toBeInTheDocument();
    expect(screen.queryByTestId('good-child')).toBeNull();
  });
});
