import { test, expect } from '@playwright/test';

test('login page loads and has correct title', async ({ page }) => {
  // We'll test against the local dev server since we are "running locally first"
  // Assuming the frontend runs on port 3000
  await page.goto('http://localhost:3000/login');

  // Verify title
  await expect(page).toHaveTitle(/SMSLY Hosting/);

  // Check for input fields
  await expect(page.locator('input[type="text"]')).toBeVisible(); // Username/Email
  await expect(page.locator('input[type="password"]')).toBeVisible(); // Password

  // Check for the "Sign in" button
  await expect(page.getByRole('button', { name: 'Sign in' })).toBeVisible();
});
