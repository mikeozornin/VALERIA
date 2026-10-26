import { expect, openAs, test } from '../fixtures';

test('the app shell opens for a signed-in persona', async ({ page, mock: _mock }) => {
    await openAs(page, 'admin', '/admin/users');
    await expect(page.locator('v-shell')).toBeVisible();
    await expect(page.locator('h1.v-screen-header__title')).toHaveText('Пользователи');
});
