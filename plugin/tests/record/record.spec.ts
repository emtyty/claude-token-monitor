import { test } from '@playwright/test';
import { DEMO_URL } from './playwright.config';

// ~28s cast + 4s tail for final scroll position to read
test.setTimeout(90_000);

test('record demo', async ({ page }) => {
  await page.goto(DEMO_URL);

  await page.waitForFunction(() => typeof (window as any).__demoDone !== 'undefined', {
    timeout: 10_000,
  });

  await page.evaluate(() => (window as any).__demoDone);

  // let the iframe report settle in frame before cutting
  await page.waitForTimeout(4000);
});
