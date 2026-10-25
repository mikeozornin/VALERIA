import { inject } from '@angular/core';
import { CanMatchFn, Route, Router, UrlSegment } from '@angular/router';
import { AuthService } from '../auth/auth.service';

function attemptedUrl(router: Router, segments: UrlSegment[]): string {
    const nav = router.currentNavigation();
    if (nav) return router.serializeUrl(nav.extractedUrl);
    return '/' + segments.map((s) => s.path).join('/');
}

/** The app shell matches only for a signed-in user; otherwise `/login?return=<url>`. */
export const authMatch: CanMatchFn = async (_route: Route, segments: UrlSegment[]) => {
    const auth = inject(AuthService);
    const router = inject(Router);
    if (await auth.ensureLoaded()) return true;
    const ret = attemptedUrl(router, segments);
    return router.createUrlTree(['/login'], { queryParams: ret && ret !== '/' ? { return: ret } : {} });
};

/** `/login` is for guests only; a signed-in user goes to the `return` URL or home. */
export const guestMatch: CanMatchFn = async () => {
    const auth = inject(AuthService);
    const router = inject(Router);
    if (!(await auth.ensureLoaded())) return true;
    const nav = router.currentNavigation();
    return router.parseUrl(safeReturnUrl(nav?.extractedUrl.queryParamMap.get('return')));
};

/** Only same-origin app paths are accepted as a return target (no `//host`, no scheme, no /login loop). */
export function safeReturnUrl(value: string | null | undefined): string {
    if (!value || !value.startsWith('/') || value.startsWith('//') || value.startsWith('/\\')) return '/';
    if (value === '/login' || value.startsWith('/login?') || value.startsWith('/login/')) return '/';
    return value;
}
