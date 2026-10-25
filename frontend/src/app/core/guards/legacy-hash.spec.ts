import { legacyHashTarget } from './legacy-hash';
import { safeReturnUrl } from './auth.guard';

describe('legacy hash links', () => {
    it('maps old hash routes to the new paths', () => {
        expect(legacyHashTarget('#/consultations/42')).toBe('/questions/42');
        expect(legacyHashTarget('#/tasks/runs/run-7')).toBe('/tasks/runs/run-7');
        expect(legacyHashTarget('#/tasks/runs/run-7/')).toBe('/tasks/runs/run-7');
        expect(legacyHashTarget('#/admin')).toBeNull();
        expect(legacyHashTarget('')).toBeNull();
        expect(legacyHashTarget('#/consultations/%E0')).toBeNull();
    });
});

describe('safeReturnUrl', () => {
    it('accepts only in-app paths', () => {
        expect(safeReturnUrl('/questions/1')).toBe('/questions/1');
        expect(safeReturnUrl('/admin/requests?user=a')).toBe('/admin/requests?user=a');
        expect(safeReturnUrl('//evil.example')).toBe('/');
        expect(safeReturnUrl('https://evil.example')).toBe('/');
        expect(safeReturnUrl('/login?return=/x')).toBe('/');
        expect(safeReturnUrl(null)).toBe('/');
    });
});
