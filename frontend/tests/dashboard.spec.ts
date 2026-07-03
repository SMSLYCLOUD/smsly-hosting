import { test, expect } from '@playwright/test';

/**
 * Dashboard smoke coverage.
 *
 * Asserts:
 *   1. Unauthenticated request is redirected to /login.
 *   2. Authenticated request renders the dashboard with mocked data
 *      and produces no 5xx responses.
 */

const AUTH_USER_API = '**/api/v1/auth/user/';
const SERVICES_API = '**/api/v1/services/';
const ADDONS_API = '**/api/v1/addons/**';
const DASHBOARD_OVERVIEW_API = '**/api/v1/dashboard/overview/';
const SYSTEM_CONFIG_API = '**/api/v1/system/config/';

function mockAuthUser(page: import('@playwright/test').Page, username = 'admin') {
  return page.route(AUTH_USER_API, async (route) => {
    await route.fulfill({
      status: 200,
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({
        pk: 1,
        username,
        email: `${username}@example.com`,
        first_name: '',
        last_name: '',
        is_staff: false,
        is_superuser: false,
        permissions: [],
      }),
    });
  });
}

function mockUnauthenticated(page: import('@playwright/test').Page) {
  return page.route(AUTH_USER_API, async (route) => {
    await route.fulfill({
      status: 401,
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ detail: 'Authentication credentials were not provided.' }),
    });
  });
}

test.describe('Dashboard page', () => {
  test('redirects unauthenticated users to /login', async ({ page }) => {
    await mockUnauthenticated(page);

    await page.goto('/dashboard');

    // The AuthProvider redirects to /login when /auth/user/ returns 401.
    await expect(page).toHaveURL(/\/login/);
  });

  test('renders the dashboard for an authenticated user without 5xx responses', async ({ page }) => {
    await page.context().addCookies([
      {
        name: 'auth_token',
        value: 'mock-token',
        domain: 'localhost',
        path: '/',
        httpOnly: false,
        secure: false,
        sameSite: 'Lax',
      },
    ]);
    await mockAuthUser(page, 'admin');

    // The dashboard's main data source.
    await page.route(DASHBOARD_OVERVIEW_API, async (route) => {
      await route.fulfill({
        status: 200,
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({
          services: { running: 2, failed: 0, stopped: 0, total: 2 },
          deployments_this_month: 5,
          addons: { active: 1, total: 1 },
          cost_estimate: { monthly_usd: 12.34, currency: 'USD' },
          system_usage: {
            cpu_percent: 25,
            ram_used_mb: 1024,
            ram_total_mb: 4096,
            storage_used_gb: 10,
            storage_total_gb: 100,
          },
          recent_activity: [],
          alerts: [],
        }),
      });
    });

    // Suppress other backend calls so the test is hermetic and avoids
    // spurious console noise / toasts during the load.
    await page.route(SERVICES_API, async (route) => {
      await route.fulfill({
        status: 200,
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ count: 0, results: [] }),
      });
    });
    await page.route(ADDONS_API, async (route) => {
      await route.fulfill({
        status: 200,
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ count: 0, results: [] }),
      });
    });
    await page.route(SYSTEM_CONFIG_API, async (route) => {
      await route.fulfill({
        status: 200,
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({}),
      });
    });

    // Collect every response and assert no 5xx is observed.
    const fiveHundreds: string[] = [];
    page.on('response', (response) => {
      if (response.status() >= 500 && response.status() < 600) {
        fiveHundreds.push(`${response.status()} ${response.url()}`);
      }
    });

    await page.goto('/dashboard');

    // Header text from the dashboard page.
    await expect(page.getByRole('heading', { name: 'Dashboard' })).toBeVisible();
    // The mocked username should appear in the welcome line.
    await expect(page.getByText(/Welcome back, admin/i)).toBeVisible();

    // No 5xx observed during the load.
    expect(fiveHundreds, `Unexpected 5xx responses:\n${fiveHundreds.join('\n')}`).toEqual([]);
  });

  test('renders the dashboard error card when /dashboard/overview/ fails', async ({ page }) => {
    await page.context().addCookies([
      {
        name: 'auth_token',
        value: 'mock-token',
        domain: 'localhost',
        path: '/',
        httpOnly: false,
        secure: false,
        sameSite: 'Lax',
      },
    ]);
    await mockAuthUser(page, 'admin');

    await page.route(DASHBOARD_OVERVIEW_API, async (route) => {
      await route.fulfill({
        status: 500,
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ detail: 'Internal server error' }),
      });
    });
    await page.route(SERVICES_API, async (route) => {
      await route.fulfill({
        status: 200,
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ count: 0, results: [] }),
      });
    });
    await page.route(ADDONS_API, async (route) => {
      await route.fulfill({
        status: 200,
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ count: 0, results: [] }),
      });
    });
    await page.route(SYSTEM_CONFIG_API, async (route) => {
      await route.fulfill({
        status: 200,
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({}),
      });
    });

    await page.goto('/dashboard');

    // The page's error card surfaces when the overview fetch fails.
    await expect(page.getByText('Dashboard Unavailable')).toBeVisible();
  });
});
