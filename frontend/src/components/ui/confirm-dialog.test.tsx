import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor, fireEvent, act } from '@testing-library/react';
import React from 'react';
import { ConfirmProvider, useConfirm } from './confirm-dialog';

// A test consumer that exposes the confirm fn via a render-prop style button
// and reports back the resolved value via a hidden DOM node.
function TestComp({
  onResult,
  options,
  triggerLabel = 'open',
}: {
  onResult?: (v: boolean) => void;
  options?: any;
  triggerLabel?: string;
}) {
  const confirm = useConfirm();
  return (
    <button
      data-testid="trigger"
      onClick={async () => {
        const v = await confirm(options);
        onResult?.(v);
        // expose the last result via a DOM node so we can read it from tests
        const node = document.querySelector('[data-testid="last-result"]');
        if (node) node.textContent = String(v);
      }}
    >
      {triggerLabel}
    </button>
  );
}

function setupLastResultNode() {
  const node = document.createElement('div');
  node.setAttribute('data-testid', 'last-result');
  document.body.appendChild(node);
  return node;
}

describe('ConfirmProvider / useConfirm', () => {
  beforeEach(() => {
    setupLastResultNode();
  });

  afterEach(() => {
    document
      .querySelectorAll('[data-testid="last-result"]')
      .forEach((n) => n.remove());
  });

  it('throws when useConfirm is called outside a provider', () => {
    // Silence the React error boundary noise for this expected throw
    const errSpy = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    expect(() => render(<TestComp />)).toThrow(
      'useConfirm must be used inside <ConfirmProvider>'
    );
    errSpy.mockRestore();
  });

  it('resolves true when the user clicks the Confirm button', async () => {
    render(
      <ConfirmProvider>
        <TestComp options={{ message: 'ship it?' }} />
      </ConfirmProvider>
    );

    fireEvent.click(screen.getByTestId('trigger'));

    const dialog = await screen.findByRole('dialog');
    expect(dialog).toBeInTheDocument();
    expect(dialog).toHaveTextContent('ship it?');

    const confirmBtn = await screen.findByRole('button', { name: /confirm/i });
    fireEvent.click(confirmBtn);

    await waitFor(() => {
      expect(
        document.querySelector('[data-testid="last-result"]')?.textContent
      ).toBe('true');
    });
  });

  it('resolves false when the user clicks Cancel', async () => {
    render(
      <ConfirmProvider>
        <TestComp options={{ message: 'abort?' }} />
      </ConfirmProvider>
    );

    fireEvent.click(screen.getByTestId('trigger'));

    await screen.findByRole('dialog');
    const cancelBtn = await screen.findByRole('button', { name: /cancel/i });
    fireEvent.click(cancelBtn);

    await waitFor(() => {
      expect(
        document.querySelector('[data-testid="last-result"]')?.textContent
      ).toBe('false');
    });
  });

  it('resolves false when the user presses Escape', async () => {
    render(
      <ConfirmProvider>
        <TestComp options={{ message: 'escape me' }} />
      </ConfirmProvider>
    );

    fireEvent.click(screen.getByTestId('trigger'));

    const dialog = await screen.findByRole('dialog');
    fireEvent.keyDown(dialog, { key: 'Escape', code: 'Escape' });

    await waitFor(() => {
      expect(
        document.querySelector('[data-testid="last-result"]')?.textContent
      ).toBe('false');
    });
  });

  it('uses the destructive button variant when variant="destructive"', async () => {
    render(
      <ConfirmProvider>
        <TestComp
          options={{ message: 'delete everything?', variant: 'destructive' }}
        />
      </ConfirmProvider>
    );

    fireEvent.click(screen.getByTestId('trigger'));

    const dialog = await screen.findByRole('dialog');
    expect(dialog).toHaveTextContent('delete everything?');

    // Find the button that will resolve true (the "Delete" / Confirm button).
    const confirmBtn = await screen.findByRole('button', { name: /delete/i });
    // Destructive buttons get the bg-red-600 class.
    expect(confirmBtn.className).toContain('bg-red-600');
  });

  it('accepts a plain string as a shorthand for { message }', async () => {
    render(
      <ConfirmProvider>
        <TestComp options="just a message" />
      </ConfirmProvider>
    );

    fireEvent.click(screen.getByTestId('trigger'));

    const dialog = await screen.findByRole('dialog');
    expect(dialog).toHaveTextContent('just a message');

    const confirmBtn = await screen.findByRole('button', { name: /confirm/i });
    fireEvent.click(confirmBtn);

    await waitFor(() => {
      expect(
        document.querySelector('[data-testid="last-result"]')?.textContent
      ).toBe('true');
    });
  });

  it('uses warning styling and custom button labels when specified', async () => {
    render(
      <ConfirmProvider>
        <TestComp
          options={{
            title: 'Restart cluster?',
            message: 'Brief interruption expected.',
            variant: 'warning',
            confirmText: 'Proceed',
            cancelText: 'Abort',
          }}
        />
      </ConfirmProvider>
    );

    fireEvent.click(screen.getByTestId('trigger'));

    const dialog = await screen.findByRole('dialog');
    expect(dialog).toHaveTextContent('Restart cluster?');
    expect(dialog).toHaveTextContent('Brief interruption expected.');

    const confirmBtn = await screen.findByRole('button', { name: 'Proceed' });
    expect(confirmBtn.className).toContain('bg-amber-600');
    expect(await screen.findByRole('button', { name: 'Abort' })).toBeInTheDocument();
  });

  describe('requireMatchText confirmation guard', () => {
    it('disables confirm until trimmed input matches requireMatchText and resolves true on confirm', async () => {
      render(
        <ConfirmProvider>
          <TestComp
            options={{
              title: 'Delete project?',
              message: 'This deletes everything.',
              variant: 'destructive',
              confirmText: 'Delete',
              requireMatchText: 'my-prod-app',
            }}
          />
        </ConfirmProvider>
      );

      fireEvent.click(screen.getByTestId('trigger'));
      await screen.findByRole('dialog');

      const confirmBtn = await screen.findByRole('button', { name: 'Delete' });
      const matchInput = screen.getByLabelText(/Type my-prod-app to confirm/i);

      expect(confirmBtn).toBeDisabled();

      fireEvent.change(matchInput, { target: { value: 'my-prod' } });
      expect(confirmBtn).toBeDisabled();

      // Enter key must not submit while input does not match
      fireEvent.keyDown(matchInput, { key: 'Enter', code: 'Enter' });
      expect(screen.getByRole('dialog')).toBeInTheDocument();

      // Matching with surrounding whitespace should satisfy the guard
      fireEvent.change(matchInput, { target: { value: '  my-prod-app  ' } });
      expect(confirmBtn).toBeEnabled();

      fireEvent.click(confirmBtn);
      await waitFor(() => {
        expect(
          document.querySelector('[data-testid="last-result"]')?.textContent
        ).toBe('true');
      });

      // Re-opening resets the typed confirmation input
      fireEvent.click(screen.getByTestId('trigger'));
      await screen.findByRole('dialog');
      expect(screen.getByLabelText(/Type my-prod-app to confirm/i)).toHaveValue('');
      expect(await screen.findByRole('button', { name: 'Delete' })).toBeDisabled();
    });

    it('submits on Enter key only when requireMatchText is satisfied', async () => {
      render(
        <ConfirmProvider>
          <TestComp
            options={{
              message: 'Confirm purge',
              confirmText: 'Purge',
              requireMatchText: 'CONFIRM',
            }}
          />
        </ConfirmProvider>
      );

      fireEvent.click(screen.getByTestId('trigger'));
      await screen.findByRole('dialog');

      const matchInput = screen.getByLabelText(/Type CONFIRM to confirm/i);
      fireEvent.change(matchInput, { target: { value: 'CONFIRM' } });
      fireEvent.keyDown(matchInput, { key: 'Enter', code: 'Enter' });

      await waitFor(() => {
        expect(
          document.querySelector('[data-testid="last-result"]')?.textContent
        ).toBe('true');
      });
    });

    it('allows cancelling without typing match text and clears input on reopen', async () => {
      render(
        <ConfirmProvider>
          <TestComp
            options={{
              message: 'Delete addon?',
              variant: 'destructive',
              confirmText: 'Delete',
              requireMatchText: 'redis-main',
            }}
          />
        </ConfirmProvider>
      );

      fireEvent.click(screen.getByTestId('trigger'));
      await screen.findByRole('dialog');

      const matchInput = screen.getByLabelText(/Type redis-main to confirm/i);
      fireEvent.change(matchInput, { target: { value: 'redis-' } });

      const cancelBtn = await screen.findByRole('button', { name: /cancel/i });
      fireEvent.click(cancelBtn);

      await waitFor(() => {
        expect(
          document.querySelector('[data-testid="last-result"]')?.textContent
        ).toBe('false');
      });

      fireEvent.click(screen.getByTestId('trigger'));
      await screen.findByRole('dialog');
      expect(screen.getByLabelText(/Type redis-main to confirm/i)).toHaveValue('');
    });
  });
});
