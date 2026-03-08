import { test, expect } from '@playwright/test';

test('login page loads and has correct title', async ({ page }) => {
  const baseUrl = process.env.BASE_URL || 'http://localhost:3000';
  await page.goto(`${baseUrl}/login`);

  // Verify title
  await expect(page).toHaveTitle(/(CloudNeuron|SMSLY Hosting)/);

  // Check for input fields
  await expect(page.locator('input[type="text"]')).toBeVisible(); // Username/Email
  await expect(page.locator('input[type="password"]')).toBeVisible(); // Password

  // Check for the "Sign in" button
  await expect(page.getByRole('button', { name: 'Sign in' })).toBeVisible();
});
