import { provideHttpClient, withInterceptors } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { Component, inject } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter, Router } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';
import { AuthService } from '../auth/auth.service';
import { adminMatch } from '../guards/role.guard';
import { ApiError } from './api-error';
import { apiInterceptor, ApiService, ON_FORBIDDEN } from './api.service';

@Component({ template: 'admin-users' })
class AdminStub {}

@Component({ template: 'Раздел недоступен' })
class DeniedStub {}

const me = (role: string) => ({
    user: { id: 1, login: 'boss', role, tasks_only: false, workspace_path: null },
    csrf_token: 't',
});

describe('403 from the API (SPEC §16.2)', () => {
    let http: HttpTestingController;

    beforeEach(() => {
        TestBed.configureTestingModule({
            providers: [
                provideHttpClient(withInterceptors([apiInterceptor])),
                provideHttpClientTesting(),
                provideRouter([
                    { path: 'admin/users', canMatch: [adminMatch], component: AdminStub },
                    { path: 'admin/users', component: DeniedStub },
                ]),
                {
                    provide: ON_FORBIDDEN,
                    useFactory: () => {
                        const auth = inject(AuthService);
                        return () => auth.handleForbidden();
                    },
                },
            ],
        });
        http = TestBed.inject(HttpTestingController);
    });

    afterEach(() => http.verify());

    async function openAsAdmin(): Promise<RouterTestingHarness> {
        const harnessPromise = RouterTestingHarness.create('/admin/users');
        await Promise.resolve();
        http.expectOne('/api/auth/me').flush(me('admin'));
        const harness = await harnessPromise;
        expect((harness.routeNativeElement as HTMLElement).textContent).toContain('admin-users');
        return harness;
    }

    const flushMicrotasks = async () => {
        for (let i = 0; i < 10; i++) await Promise.resolve();
    };

    it('a demoted admin gets «Раздел недоступен», not the backend 403', async () => {
        const harness = await openAsAdmin();
        const api = TestBed.inject(ApiService);
        const outcome = vi.fn();
        api.get('/api/admin/users').then(
            () => outcome('resolved'),
            (e) => outcome(e),
        );

        http.expectOne('/api/admin/users').flush({ detail: 'Administrator role required' }, { status: 403, statusText: 'Forbidden' });
        await flushMicrotasks();
        http.expectOne('/api/auth/me').flush(me('user'));
        await vi.waitFor(() => {
            harness.detectChanges();
            expect((harness.routeNativeElement as HTMLElement).textContent).toContain('Раздел недоступен');
        });
        expect(TestBed.inject(Router).url).toBe('/admin/users');
        expect(outcome).not.toHaveBeenCalled();
    });

    it('when access did not change, the caller gets the 403 as usual', async () => {
        const harness = await openAsAdmin();
        const api = TestBed.inject(ApiService);
        const failure = api.get('/api/admin/users').catch((e: unknown) => e);

        http.expectOne('/api/admin/users').flush({ detail: 'CSRF check failed' }, { status: 403, statusText: 'Forbidden' });
        await flushMicrotasks();
        http.expectOne('/api/auth/me').flush(me('admin'));

        const error = await failure;
        expect(error).toBeInstanceOf(ApiError);
        expect((error as ApiError).status).toBe(403);
        harness.detectChanges();
        expect((harness.routeNativeElement as HTMLElement).textContent).toContain('admin-users');
    });
});
