import { test, expect } from '@playwright/test';

/**
 * Registration flow e2e coverage.
 *
 * The register page is a single-view form with username, email, and two
 * password fields. Client-side validation must reject mismatched passwords
 * before any network call; the backend's throttled envelope and field-level
 * errors are mirrored on the page.
 */

const REGISTER_API = '**/api/v1/auth/registration/';

async function fillRegistrationForm(
  page: import('@playwright/test').Page,
  values: { username: string; email: string; password1: string; password2: string }
) {
  await page.getByLabel('Username', { exact: true }).fill(values.username);
  await page.getByLabel('Email', { exact: true }).fill(values.email);
  await page.getByLabel('Password', { exact: true }).fill(values.password1);
  await page.getByLabel('Confirm Password', { exact: true }).fill(values.password2);
}

test.describe('Register page', () => {
  test('renders the registration form with all fields and submit button', async ({ page }) => {
    await page.goto('/register');

    await expect(page).toHaveTitle(/Grid|SMSLY Hosting/i);

    await expect(page.getByLabel('Username', { exact: true })).toBeVisible();
    await expect(page.getByLabel('Email', { exact: true })).toBeVisible();
    await expect(page.getByLabel('Password', { exact: true })).toBeVisible();
    await expect(page.getByLabel('Confirm Password', { exact: true })).toBeVisible();

    await expect(
      page.getByRole('button', { name: /Sign up with Email/i })
    ).toBeVisible();
  });

  test('rejects mismatched passwords client-side without calling the API', async ({ page }) => {
    await page.goto('/register');

    // Track network calls to assert none are made for the mismatched-password
    // case — the validation must happen before fetch().
    let registerApiCalled = false;
    await page.route(REGISTER_API, async (route) => {
      registerApiCalled = true;
      await route.fulfill({
        status: 200,
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ key: 'fake-token' }),
      });
    });

    await fillRegistrationForm(page, {
      username: 'newuser',
      email: 'newuser@example.com',
      password1: 'Sup3rSecret!',
      password2: 'DIFFERENT-PASSWORD',
    });

    await page.getByRole('button', { name: /Sign up with Email/i }).click();

    // The exact UI string from src/app/register/page.tsx.
    await expect(page.getByText('Passwords do not match.', { exact: true })).toBeVisible();

    // The page should not have called the registration endpoint.
    await page.waitForTimeout(200);
    expect(registerApiCalled).toBe(false);
  });

  test('rate-limit regression: shows friendly throttled message for registration', async ({ page }) => {
    await page.goto('/register');

    await page.route(REGISTER_API, async (route) => {
      await route.fulfill({
        status: 429,
        headers: {
          'Retry-After': '60',
          'content-type': 'application/json',
        },
        body: JSON.stringify({
          code: 'throttled',
          wait_seconds: 60,
          status: 429,
        }),
      });
    });

    await fillRegistrationForm(page, {
      username: 'newuser',
      email: 'newuser@example.com',
      password1: 'Sup3rSecret!',
      password2: 'Sup3rSecret!',
    });

    await page.getByRole('button', { name: /Sign up with Email/i }).click();

    await expect(page.getByText(/Too many registration attempts/i)).toBeVisible();
    await expect(page.getByText(/wait .* minute\(s\) before trying again/i)).toBeVisible();
    await expect(page.getByText('Passwords do not match.', { exact: true })).toHaveCount(0);
  });

  test('rate-limit regression: handles missing wait_seconds gracefully', async ({ page }) => {
    await page.goto('/register');

    await page.route(REGISTER_API, async (route) => {
      await route.fulfill({
        status: 429,
        headers: {
          'Retry-After': '60',
          'content-type': 'application/json',
        },
        body: JSON.stringify({
          code: 'throttled',
          status: 429,
        }),
      });
    });

    await fillRegistrationForm(page, {
      username: 'newuser',
      email: 'newuser@example.com',
      password1: 'Sup3rSecret!',
      password2: 'Sup3rSecret!',
    });

    await page.getByRole('button', { name: /Sign up with Email/i }).click();

    await expect(page.getByText(/Too many registration attempts/i)).toBeVisible();
  });

  test('generic 4xx with field errors: surfaces the first field error verbatim', async ({ page }) => {
    await page.goto('/register');

    await page.route(REGISTER_API, async (route) => {
      await route.fulfill({
        status: 400,
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({
          username: ['A user with that username already exists.'],
        }),
      });
    });

    await fillRegistrationForm(page, {
      username: 'takenuser',
      email: 'taken@example.com',
      password1: 'Sup3rSecret!',
      password2: 'Sup3rSecret!',
    });

    await page.getByRole('button', { name: /Sign up with Email/i }).click();

    // The exact message text must reach the user so they can correct the
    // offending field. Substring match because drf-style field errors may
    // also include surrounding punctuation.
    await expect(page.getByText(/already exists/i)).toBeVisible();
  });

  test('generic 4xx without structured error: falls back to generic message', async ({ page }) => {
    await page.goto('/register');

    await page.route(REGISTER_API, async (route) => {
      await route.fulfill({
        status: 500,
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({}),
      });
    });

    await fillRegistrationForm(page, {
      username: 'newuser',
      email: 'newuser@example.com',
      password1: 'Sup3rSecret!',
      password2: 'Sup3rSecret!',
    });

    await page.getByRole('button', { name: /Sign up with Email/i }).click();

    // Hard-coded fallback when no structured error is provided.
    await expect(
      page.getByText('Registration failed. Please check your input.', { exact: true })
    ).toBeVisible();
  });
});
