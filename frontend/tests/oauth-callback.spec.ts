import { test, expect } from '@playwright/test';

/**
 * OAuth callback e2e coverage.
 *
 * The /auth/github/callback page reads `code` (and `state`) from the URL,
 * POSTs them to /api/v1/integrations/github/oauth-callback/, then either:
 *   - shows a success state and (after a delay) redirects to /settings, or
 *   - shows an error state with the message from the backend and stays put.
 *
 * The api client uses an axios instance with credentials, so we don't need
 * to set cookies manually for these tests.
 */

const OAUTH_CALLBACK_API = '**/api/v1/integrations/github/oauth-callback/';

test.describe('OAuth callback (GitHub)', () => {
  test('success path: shows success state and navigates to /settings', async ({ page }) => {
    await page.route(OAUTH_CALLBACK_API, async (route) => {
      await route.fulfill({
        status: 200,
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({
          account: { login: 'octocat' },
        }),
      });
    });

    await page.goto('/auth/github/callback?code=abc&state=xyz');

    // Success message includes the GitHub login.
    await expect(page.getByText(/GitHub connected as octocat/i)).toBeVisible();
    await expect(page.getByText('Redirecting to settings...')).toBeVisible();

    // The success branch eventually navigates to /settings (after a short
    // setTimeout). Allow it up to the test timeout.
    await page.waitForURL(/\/settings/, { timeout: 5000 });
  });

  test('failure path: shows backend error message and stays on the callback page', async ({ page }) => {
    await page.route(OAUTH_CALLBACK_API, async (route) => {
      await route.fulfill({
        status: 400,
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({
          error: 'Invalid state',
        }),
      });
    });

    await page.goto('/auth/github/callback?code=stale&state=expired');

    // The page reads `error` from the response body and renders it under
    // the "GitHub Connection Failed" heading.
    await expect(page.getByText('GitHub Connection Failed')).toBeVisible();
    await expect(page.getByText('Invalid state', { exact: true })).toBeVisible();

    // The page must not navigate away — the failure branch keeps the user
    // on the callback URL with a "Return to Settings" link.
    await expect(page).toHaveURL(/\/auth\/github\/callback/);
    await expect(page.getByRole('button', { name: 'Return to Settings' })).toBeVisible();
  });

  test('no code: renders an error without calling the backend', async ({ page }) => {
    // Track network calls to verify the page does NOT call the API when
    // the `code` query param is absent — this is a client-side guard.
    let apiCalled = false;
    await page.route(OAUTH_CALLBACK_API, async (route) => {
      apiCalled = true;
      await route.fulfill({
        status: 200,
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ account: { login: 'should-not-happen' } }),
      });
    });

    await page.goto('/auth/github/callback');

    await expect(page.getByText('GitHub Connection Failed')).toBeVisible();
    await expect(
      page.getByText('No authorization code received from GitHub.', { exact: true })
    ).toBeVisible();

    // Give the page a moment — it should not have called the API.
    await page.waitForTimeout(200);
    expect(apiCalled).toBe(false);
  });

  test('denied: renders the OAuth provider error description', async ({ page }) => {
    // The page also handles the `?error=` query param the provider sets when
    // the user denies authorization. No backend call should happen.
    let apiCalled = false;
    await page.route(OAUTH_CALLBACK_API, async (route) => {
      apiCalled = true;
      await route.fulfill({
        status: 200,
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ account: { login: 'should-not-happen' } }),
      });
    });

    await page.goto(
      '/auth/github/callback?error=access_denied&error_description=The+user+denied+the+request'
    );

    await expect(page.getByText('GitHub Connection Failed')).toBeVisible();
    await expect(page.getByText('The user denied the request', { exact: true })).toBeVisible();

    await page.waitForTimeout(200);
    expect(apiCalled).toBe(false);
  });
});
