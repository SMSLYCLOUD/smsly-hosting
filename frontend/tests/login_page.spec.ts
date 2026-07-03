import { test, expect } from '@playwright/test';

/**
 * Login flow e2e coverage.
 *
 * The login page has a multi-step layout:
 *   1. Landing view shows social buttons + a "Sign in with Email" CTA.
 *   2. Clicking the email CTA reveals a username/email + password form.
 *
 * The backend rate-limit envelope is
 *   { error, code: "throttled", status, wait_seconds }
 * with a `Retry-After` header. The UI surfaces a friendly message
 * derived from `wait_seconds` instead of the generic
 * "Invalid username/email or password" fallback.
 */

const LOGIN_API = '**/api/v1/auth/login/';

async function openEmailForm(page: import('@playwright/test').Page) {
  // The landing view has multiple "Sign in with ..." buttons; the email one
  // is the in-page button (not the GitHub/Google/GitLab/Bitbucket anchors).
  await page.getByRole('button', { name: 'Sign in with Email' }).click();
  await expect(page.getByLabel('Username or Email')).toBeVisible();
  await expect(page.getByLabel('Password')).toBeVisible();
}

async function submitCredentials(
  page: import('@playwright/test').Page,
  username: string,
  password: string
) {
  await page.getByLabel('Username or Email').fill(username);
  await page.getByLabel('Password').fill(password);
  await page.getByRole('button', { name: 'Sign in', exact: true }).click();
}

test.describe('Login page', () => {
  test('renders landing view with expected title and email CTA', async ({ page }) => {
    await page.goto('/login');

    // Title contains either the brand ("Grid") or the product name.
    await expect(page).toHaveTitle(/Grid|SMSLY Hosting/i);

    // Landing view should expose the social providers and an email CTA.
    await expect(page.getByRole('button', { name: 'Sign in with Email' })).toBeVisible();
    await expect(page.getByRole('link', { name: /Sign in with GitHub/i })).toBeVisible();
  });

  test('switches to email form and exposes username + password inputs', async ({ page }) => {
    await page.goto('/login');
    await openEmailForm(page);

    // The form should expose a "Back to options" affordance so users can
    // retreat to the social-login landing view.
    await expect(page.getByRole('button', { name: 'Back to options' })).toBeVisible();
  });

  test('rate-limit regression: shows friendly throttled message (not the wrong-password fallback)', async ({ page }) => {
    await page.goto('/login');
    await openEmailForm(page);

    // Mock the backend throttled response — the 429 envelope used by the
    // shared rate-limit fix. The UI must compute minutes from `wait_seconds`
    // and render a distinct message instead of the generic fallback.
    await page.route(LOGIN_API, async (route) => {
      await route.fulfill({
        status: 429,
        headers: {
          'Retry-After': '30',
          'content-type': 'application/json',
        },
        body: JSON.stringify({
          error: 'Too Many Requests',
          code: 'throttled',
          status: 429,
          wait_seconds: 30,
        }),
      });
    });

    await submitCredentials(page, 'admin', 'wrong-password');

    // The friendly rate-limit message must be visible, NOT the generic
    // "Invalid username/email or password" fallback that masks throttling.
    await expect(page.getByText(/Too many login attempts/i)).toBeVisible();
    await expect(page.getByText(/wait .* minute\(s\) before trying again/i)).toBeVisible();
    await expect(
      page.getByText('Invalid username/email or password', { exact: true })
    ).toHaveCount(0);
  });

  test('rate-limit regression: handles missing wait_seconds gracefully', async ({ page }) => {
    await page.goto('/login');
    await openEmailForm(page);

    // Some backends may omit wait_seconds — the UI should still show a
    // distinct throttled message instead of falling through to the
    // wrong-password fallback.
    await page.route(LOGIN_API, async (route) => {
      await route.fulfill({
        status: 429,
        headers: {
          'Retry-After': '60',
          'content-type': 'application/json',
        },
        body: JSON.stringify({
          error: 'Too Many Requests',
          code: 'throttled',
          status: 429,
        }),
      });
    });

    await submitCredentials(page, 'admin', 'whatever');

    await expect(page.getByText(/Too many login attempts/i)).toBeVisible();
  });

  test('generic 4xx: surfaces non_field_errors from the API', async ({ page }) => {
    await page.goto('/login');
    await openEmailForm(page);

    await page.route(LOGIN_API, async (route) => {
      await route.fulfill({
        status: 400,
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({
          non_field_errors: ['Unable to log in with provided credentials.'],
        }),
      });
    });

    await submitCredentials(page, 'admin', 'wrong-password');

    // The API's first non_field_errors entry must reach the user verbatim.
    await expect(
      page.getByText('Unable to log in with provided credentials.', { exact: true })
    ).toBeVisible();
    // And we must not surface the throttled message for a non-429 response.
    await expect(page.getByText(/Too many login attempts/i)).toHaveCount(0);
  });

  test('generic 4xx: falls back when no structured error is provided', async ({ page }) => {
    await page.goto('/login');
    await openEmailForm(page);

    await page.route(LOGIN_API, async (route) => {
      await route.fulfill({
        status: 400,
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({}),
      });
    });

    await submitCredentials(page, 'admin', 'wrong-password');

    // Empty body -> the page renders its hard-coded fallback so the user
    // is not left staring at an empty form.
    await expect(
      page.getByText('Invalid username/email or password', { exact: true })
    ).toBeVisible();
  });
});
