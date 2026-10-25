import { ChangeDetectionStrategy, Component, computed, DestroyRef, inject, signal } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { FormControl, FormGroup, ReactiveFormsModule } from '@angular/forms';
import { Router, RouterLink } from '@angular/router';
import { KbqAlertColors, KbqAlertModule } from '@koobiq/components/alert';
import { KbqButtonModule } from '@koobiq/components/button';
import { KbqComponentColors, PopUpPlacements } from '@koobiq/components/core';
import { KbqFormFieldModule } from '@koobiq/components/form-field';
import { KbqInputModule } from '@koobiq/components/input';
import { KbqRadioModule } from '@koobiq/components/radio';
import { KbqToolTipModule } from '@koobiq/components/tooltip';
import { ApiError } from '../../../core/api/api-error';
import { ApiService } from '../../../core/api/api.service';
import { DirtyAware, DirtyFormService } from '../../../core/guards/dirty.guard';
import { NotifyService } from '../../../core/notify/notify.service';
import { describeError } from '../../../core/status/error-codes';
import { ScreenHeaderComponent } from '../../../shell/screen-header/screen-header.component';
import { ACCOUNT_TYPES, AccountType, accountTypeToApi, AdminUser } from '../shared/admin.models';
import { ADMIN_FORM_STYLES } from '../shared/admin-styles';
import { SyncOp, SyncSlowAlertComponent } from '../shared/sync-op';

const MIN_PASSWORD = 12;

/** «Новый пользователь» (`/admin/users/new`, SPEC §10.2). */
@Component({
    selector: 'v-admin-user-create',
    imports: [
        ReactiveFormsModule,
        RouterLink,
        ScreenHeaderComponent,
        SyncSlowAlertComponent,
        KbqAlertModule,
        KbqButtonModule,
        KbqFormFieldModule,
        KbqInputModule,
        KbqRadioModule,
        KbqToolTipModule,
    ],
    changeDetection: ChangeDetectionStrategy.OnPush,
    template: `
        <v-screen-header title="Новый пользователь" />

        <form class="v-formscroll" [formGroup]="form" (ngSubmit)="create()" novalidate>
            <div class="v-form">
                <v-sync-slow-alert [op]="op" />
                @if (saveError(); as e) {
                    <kbq-alert [alertColor]="alertColors.Error">
                        <div kbq-alert-title>Не удалось создать пользователя</div>
                        {{ e }}
                    </kbq-alert>
                }

                <fieldset class="v-fsec">
                    <legend class="v-fsec__title">Вход</legend>
                    <div class="v-frow">
                        <label class="v-frow__label" for="v-login">Логин <span class="v-req">*</span></label>
                        <div class="v-frow__ctl">
                            <kbq-form-field class="v-w320">
                                <input
                                    kbqInput
                                    id="v-login"
                                    formControlName="login"
                                    autocomplete="off"
                                    [required]="true"
                                    [attr.aria-invalid]="!!fieldError('login')"
                                />
                            </kbq-form-field>
                            @if (fieldError('login'); as e) {
                                <div class="v-hint v-hint--error" role="alert">{{ e }}</div>
                            }
                        </div>
                    </div>
                    <div class="v-frow">
                        <label class="v-frow__label" for="v-pass">Временный пароль <span class="v-req">*</span></label>
                        <div class="v-frow__ctl">
                            <kbq-form-field class="v-w320">
                                <input
                                    kbqInputPassword
                                    id="v-pass"
                                    formControlName="password"
                                    placeholder="Не менее 12 символов"
                                    autocomplete="new-password"
                                    [required]="true"
                                    aria-describedby="v-pass-hint"
                                />
                                <kbq-password-toggle />
                            </kbq-form-field>
                            @if (fieldError('password'); as e) {
                                <div class="v-hint v-hint--error" role="alert">{{ e }}</div>
                            }
                            <div class="v-hint" id="v-pass-hint">
                                VALERIA его никуда не отправляет, передайте пароль пользователю сами.
                            </div>
                        </div>
                    </div>
                    <div class="v-frow">
                        <div class="v-frow__label" id="v-type-label">Тип учётки</div>
                        <div class="v-frow__ctl">
                            <kbq-radio-group
                                class="v-check-group"
                                formControlName="type"
                                aria-labelledby="v-type-label"
                            >
                                @for (t of types; track t.id) {
                                    <kbq-radio-button [value]="t.id">
                                        {{ t.label }}<span class="v-check-sub">{{ t.hint }}</span>
                                    </kbq-radio-button>
                                }
                            </kbq-radio-group>
                        </div>
                    </div>
                </fieldset>

                <fieldset class="v-fsec">
                    <legend class="v-fsec__title">Работа</legend>
                    <div class="v-frow">
                        <label class="v-frow__label" for="v-ws">Рабочая папка</label>
                        <div class="v-frow__ctl">
                            <kbq-form-field>
                                <input
                                    kbqInput
                                    kbqInputMonospace
                                    id="v-ws"
                                    formControlName="ws"
                                    placeholder="team/project"
                                    aria-describedby="v-ws-hint"
                                />
                            </kbq-form-field>
                            @if (fieldError('ws'); as e) {
                                <div class="v-hint v-hint--error" role="alert">{{ e }}</div>
                            }
                            <div class="v-hint" id="v-ws-hint">
                                Путь внутри каталога данных сервера. Если папки нет, она создастся.
                            </div>
                        </div>
                    </div>
                    <div class="v-frow">
                        <label class="v-frow__label" for="v-mm">Mattermost</label>
                        <div class="v-frow__ctl">
                            <kbq-form-field class="v-w320">
                                <input
                                    kbqInput
                                    id="v-mm"
                                    formControlName="mm"
                                    placeholder="Логин без @"
                                    aria-describedby="v-mm-hint"
                                />
                            </kbq-form-field>
                            @if (fieldError('mm'); as e) {
                                <div class="v-hint v-hint--error" role="alert">{{ e }}</div>
                            }
                            <div class="v-hint" id="v-mm-hint">
                                Сюда Валерия пишет о готовых ответах и задачах. Можно указать позже.
                            </div>
                        </div>
                    </div>
                </fieldset>
            </div>

            <div class="v-formfoot">
                <span
                    [kbqTooltip]="'Укажите логин и пароль не короче 12 символов'"
                    [kbqTooltipDisabled]="ready()"
                    [kbqPlacement]="placements.Top"
                >
                    <button
                        kbq-button
                        type="submit"
                        [color]="colors.Theme"
                        [disabled]="!ready() || op.busy()"
                        [class.kbq-progress]="op.busy()"
                    >
                        Создать
                    </button>
                </span>
                <a kbq-button routerLink="/admin/users">Отмена</a>
            </div>
        </form>
    `,
    styles: [ADMIN_FORM_STYLES],
})
export class UserCreateComponent implements DirtyAware {
    private readonly api = inject(ApiService);
    private readonly router = inject(Router);
    private readonly notify = inject(NotifyService);

    protected readonly colors = KbqComponentColors;
    protected readonly alertColors = KbqAlertColors;
    protected readonly placements = PopUpPlacements;
    protected readonly types = ACCOUNT_TYPES;
    protected readonly op = new SyncOp();

    protected readonly form = new FormGroup({
        login: new FormControl('', { nonNullable: true }),
        password: new FormControl('', { nonNullable: true }),
        type: new FormControl<AccountType>('user', { nonNullable: true }),
        ws: new FormControl('', { nonNullable: true }),
        mm: new FormControl('', { nonNullable: true }),
    });
    private readonly value = toSignal(this.form.valueChanges, { initialValue: this.form.getRawValue() });
    protected readonly ready = computed(
        () => !!this.value().login?.trim() && (this.value().password ?? '').length >= MIN_PASSWORD,
    );
    protected readonly saveError = signal<string | null>(null);
    private readonly serverErrors = signal<Partial<Record<'login' | 'password' | 'ws' | 'mm', string>>>({});
    private created = false;

    constructor() {
        const untrack = inject(DirtyFormService).track(() => this.isDirty());
        inject(DestroyRef).onDestroy(untrack);
        for (const key of ['login', 'password', 'ws', 'mm'] as const) {
            this.form.controls[key].valueChanges.subscribe(() => this.clearField(key));
        }
    }

    isDirty(): boolean {
        if (this.created) return false;
        const v = this.form.getRawValue();
        return !!(v.login.trim() || v.password || v.ws.trim() || v.mm.trim() || v.type !== 'user');
    }

    protected fieldError(field: 'login' | 'password' | 'ws' | 'mm'): string | null {
        return this.serverErrors()[field] ?? null;
    }

    private clearField(field: 'login' | 'password' | 'ws' | 'mm'): void {
        if (!this.serverErrors()[field]) return;
        const next = { ...this.serverErrors() };
        delete next[field];
        this.serverErrors.set(next);
    }

    protected async create(): Promise<void> {
        if (!this.ready() || this.op.busy()) return;
        this.saveError.set(null);
        const v = this.form.getRawValue();
        const login = v.login.trim();
        const ws = v.ws.trim();
        const mm = v.mm.trim().replace(/^@/, '');
        if (v.type !== 'admin' && !ws) {
            this.serverErrors.set({ ws: 'Укажите рабочую папку' });
            return;
        }
        this.serverErrors.set({});
        const body: Record<string, unknown> = { login, password: v.password, ...accountTypeToApi(v.type) };
        if (ws) body['workspace_path'] = ws;
        if (mm) body['mattermost_username'] = mm;

        const result = await this.op.run(() => this.api.post<AdminUser>('/api/admin/users', body));
        if (result.ok) {
            this.created = true;
            await this.router.navigate(['/admin/users'], { queryParams: { focus: result.value.login ?? login } });
            this.notify.success('Пользователь создан', `${login} войдёт с временным паролем`);
            return;
        }
        if ('error' in result) await this.showError(result.error, login, mm);
    }

    private async showError(err: unknown, login: string, mm: string): Promise<void> {
        if (err instanceof ApiError && err.isConflict) {
            // The backend says «Login or Mattermost user already exists»: find out which one from the user list.
            let users: AdminUser[] = [];
            try {
                users = await this.api.get<AdminUser[]>('/api/admin/users');
            } catch {
                // Fall back to the login message.
            }
            const loginTaken = users.some((u) => u.login === login);
            const mmTaken = !!mm && users.some((u) => (u.mattermost_username ?? '').toLowerCase() === mm.toLowerCase());
            if (!loginTaken && mmTaken) {
                this.serverErrors.set({ mm: `Mattermost @${mm} уже привязан к другому пользователю` });
            } else {
                this.serverErrors.set({ login: `Логин ${login} уже занят` });
            }
            return;
        }
        if (err instanceof ApiError && err.status === 422 && err.detail === 'Workspace is required for a user') {
            this.serverErrors.set({ ws: 'Укажите рабочую папку' });
            return;
        }
        this.saveError.set(describeError(err).text);
    }
}
