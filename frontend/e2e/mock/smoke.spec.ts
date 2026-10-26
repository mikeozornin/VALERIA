import { expect, openAs, test } from '../fixtures';

test('the app opens for a signed-in user', async ({ page, mock }) => {
    await page.goto('/');
    await expect(page.locator('v-shell')).toBeVisible();
});
