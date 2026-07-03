import { describe, it, expect, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import React from 'react';

// Mock the heavy FloatingAI module BEFORE importing the loader.
vi.mock('@/components/ai/FloatingAI', () => ({
  FloatingAI: () => <div data-testid="floating-ai-mock">AI</div>,
}));

import FloatingAILoader from './FloatingAILoader';

describe('FloatingAILoader', () => {
  it('default export is a React component (function or forwardRef object)', () => {
    expect(FloatingAILoader).toBeDefined();
    // next/dynamic returns either a LoadableComponent object (with a render
    // fn or $$typeof for forwardRef/memo) or a plain function. Either way
    // it must be "callable" / recognizable as a component.
    const isForwardRef = (x: any) =>
      typeof x === 'object' && x !== null && x.$$typeof;
    const isFunction = typeof FloatingAILoader === 'function';
    expect(Boolean(isFunction || isForwardRef(FloatingAILoader))).toBe(true);
  });

  it('renders the loading skeleton initially', () => {
    const { container } = render(<FloatingAILoader />);

    // The Skeleton component renders a div with the provided className. The
    // exact element depends on the Skeleton impl — find by class signature.
    const skeleton = container.querySelector(
      '.h-14.w-14.rounded-full.fixed.bottom-4.right-4.z-50'
    );
    expect(skeleton).toBeInTheDocument();

    // While loading, the heavy widget must not yet be visible.
    expect(screen.queryByTestId('floating-ai-mock')).toBeNull();
  });

  it('eventually renders the FloatingAI widget', async () => {
    render(<FloatingAILoader />);

    await waitFor(
      () => {
        expect(screen.getByTestId('floating-ai-mock')).toBeInTheDocument();
      },
      { timeout: 2000 }
    );
  });
});
