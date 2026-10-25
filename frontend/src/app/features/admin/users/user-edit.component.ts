import {
    ChangeDetectionStrategy,
    Component,
    computed,
    DestroyRef,
    effect,
    inject,
    input,
    signal,
    untracked,
} from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { FormControl, FormGroup, ReactiveFormsModule } from '@angular/forms';
import { Router, RouterLink } from '@angular/router';
import { KbqAlertColors, KbqAlertModule } from '@koobiq/components/alert';
import { KbqButtonModule } from '@koobiq/components/button';
import { KbqCheckboxModule } from '@koobiq/components/checkbox';
import { KbqComponentColors } from '@koobiq/components/core';
import { KbqFormFieldModule } from '@koobiq/components/form-field';
import { KbqInputModule } from '@koobiq/components/input';
import { KbqRadioModule } from '@koobiq/components/radio';
import { KbqToggleModule } from '@koobiq/components/toggle';
import { ApiError } from '../../../core/api/api-error';
import { ApiService } from '../../../core/api/api.service';
import { DirtyAware, DirtyFormService } from '../../../core/guards/dirty.guard';
import { NotifyService } from '../../../core/notify/notify.service';
import { describeError } from '../../../core/status/error-codes';
import { statusLabel } from '../../../core/status/status';
import { PageStateComponent } from '../../../shared/page-state/page-state.component';
import { ScreenHeaderComponent } from '../../../shell/screen-header/screen-header.component';
import {
    ACCOUNT_TYPES,
    AccountType,
    accountType,
    accountTypeToApi,
    AdminJob,
    AdminTask,
    AdminUser,
    ConflictInfo,
    conflictText,
    hasTaskAccess,
    isBlocked,
    readConflict,
    UNFINISHED_JOB_STATUSES,
} from '../shared/admin.models';
import { ADMIN_FORM_STYLES } from '../shared/admin-styles';
import { SyncOp, SyncSlowAlertComponent } from '../shared/sync-op';

const WS_BUSY = 'Workspace cannot change while jobs are queued or active';
const MM_TAKEN = 'Mattermost user is already assigned';

interface FormSnapshot {
    type: AccountType;
    blocked: boolean;
    ws: string;
    limit: string;
    mm: string;
    tasks: string;
}

/** «Изменение пользователя» (`/admin/users/:login/edit`, SPEC §10.2): sync form with optimistic concurrency. */
@Component({
    selector: 'v-admin-user-edit',
    imports: [
        ReactiveFormsModule,
        RouterLink,
        ScreenHeaderComponent,
        PageStateComponent,
        SyncSlowAlertComponent,
        KbqAlertModule,
        KbqButtonModule,
        KbqCheckboxModule,
        KbqFormFieldModule,
        KbqInputModule,
        KbqRadioModule,
        KbqToggleModule,
    ],
    changeDetection: ChangeDetectionStrategy.OnPush,
    template: `
        <v-screen-header [title]="login()">
            <span vHeaderAfterTitle class="v-secondary">Изменение</span>
        </v-screen-header>

        <v-page-state
            class="v-page"
            [loading]="loading()"
            [error]="loadError()"
            [errorTitle]="loadErrorTitle()"
            (retry)="load()"
        >
            @if (user(); as u) {
                <form class="v-formscroll" [formGroup]="form" (ngSubmit)="save()" novalidate>
                    <div class="v-form">
                        <v-sync-slow-alert [op]="op" />
                        @if (conflict(); as c) {
                            <kbq-alert [alertColor]="alertColors.Error">
                                <div kbq-alert-title>{{ conflictTitle(c) }}</div>
                                @if (c.kind === 'conflict') {
                                    <div kbq-alert-control>
                                        <button
                                            kbq-button
                                            type="button"
                                            [color]="colors.ContrastFade"
                                            [disabled]="op.busy()"
                                            (click)="overwrite(c)"
                                        >
                                            Перезаписать
                                        </button>
                                    </div>
                                }
                            </kbq-alert>
                        }
                        @if (wsJobs(); as jobs) {
                            <kbq-alert [alertColor]="alertColors.Error">
                                <div kbq-alert-title>
                                    Папку не сменить, пока у {{ u.login }} есть задания в очереди или в работе
                                </div>
                                {{ jobsText(jobs) }}Дождитесь, пока они закончатся, или остановите их. Остальные
                                изменения не сохранены.
                                <div kbq-alert-control>
                                    <a kbq-button [color]="colors.ContrastFade" routerLink="/admin/queue"
                                        >Открыть очередь</a
                                    >
                                </div>
                            </kbq-alert>
                        }
                        @if (saveError(); as e) {
                            <kbq-alert [alertColor]="alertColors.Error">
                                <div kbq-alert-title>Не удалось сохранить</div>
                                {{ e }}
                            </kbq-alert>
                        }

                        <fieldset class="v-fsec">
                            <legend class="v-fsec__title">Учётка</legend>
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
                            <div class="v-frow">
                                <div class="v-frow__label">Блокировка</div>
                                <div class="v-frow__ctl">
                                    <kbq-toggle class="v-pt6" formControlName="blocked" aria-describedby="v-block-hint"
                                        >Заблокирован</kbq-toggle
                                    >
                                    <div class="v-hint" id="v-block-hint">
                                        Заблокированный пользователь не войдёт в VALERIA, его открытые сессии
                                        завершатся. Чаты и запуски сохранятся.
                                    </div>
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
                                            aria-describedby="v-ws-hint"
                                        />
                                    </kbq-form-field>
                                    @if (fieldError('ws'); as e) {
                                        <div class="v-hint v-hint--error" role="alert">{{ e }}</div>
                                    }
                                    <div class="v-hint" id="v-ws-hint">
                                        Путь внутри каталога данных сервера. При смене папки чаты из старой уйдут в
                                        архив пользователя.
                                    </div>
                                </div>
                            </div>
                            <div class="v-frow">
                                <label class="v-frow__label" for="v-limit">Лимит задач</label>
                                <div class="v-frow__ctl">
                                    <kbq-form-field class="v-w160">
                                        <input
                                            kbqInput
                                            id="v-limit"
                                            formControlName="limit"
                                            inputmode="numeric"
                                            [placeholder]="'Общий: ' + (globalLimit() ?? '—')"
                                            aria-describedby="v-limit-hint"
                                        />
                                    </kbq-form-field>
                                    @if (fieldError('limit'); as e) {
                                        <div class="v-hint v-hint--error" role="alert">{{ e }}</div>
                                    }
                                    <div class="v-hint" id="v-limit-hint">
                                        Сколько задач выполняется одновременно, от 1 до {{ hardLimit() }}. Пусто —
                                        действует общий лимит.
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
                                        @if (mmValue().trim()) {
                                            Валерия пишет сюда о готовых ответах и задачах.
                                        } @else {
                                            Не настроено: Валерия не сможет написать пользователю, когда ответ или
                                            задача будут готовы.
                                        }
                                    </div>
                                </div>
                            </div>
                        </fieldset>

                        <fieldset class="v-fsec">
                            <legend class="v-fsec__title">Доступ к задачам</legend>
                            <div class="v-frow">
                                <div class="v-frow__label" id="v-tasks-label">Задачи</div>
                                <div class="v-frow__ctl">
                                    <div class="v-check-group" role="group" aria-labelledby="v-tasks-label">
                                        @for (t of tasks(); track t.id) {
                                            <kbq-checkbox
                                                [checked]="t.visibility === 'all' || access().has(t.id)"
                                                [disabled]="t.visibility === 'all' || op.busy()"
                                                (change)="toggleTask(t.id)"
                                            >
                                                {{ t.title }}
                                                @if (t.visibility === 'all') {
                                                    <span class="v-check-sub">Открыта всем</span>
                                                } @else if (!t.is_published) {
                                                    <span class="v-check-sub">Черновик</span>
                                                }
                                            </kbq-checkbox>
                                        }
                                    </div>
                                    <div class="v-hint">
                                        То же самое для всех пользователей сразу — во вкладке «Доступ» настройки задач.
                                    </div>
                                </div>
                            </div>
                        </fieldset>
                    </div>

                    <div class="v-formfoot">
                        <button
                            kbq-button
                            type="submit"
                            [color]="colors.Theme"
                            [disabled]="op.busy()"
                            [class.kbq-progress]="op.busy()"
                        >
                            Сохранить
                        </button>
                        <a kbq-button routerLink="/admin/users" [queryParams]="{ focus: u.login }">Отмена</a>
                    </div>
                </form>
            }
        </v-page-state>
    `,
    styles: [
        ADMIN_FORM_STYLES,
        `
            .v-page {
                display: flex;
                flex-direction: column;
                flex: 1 1 auto;
                min-height: 0;
            }
            .v-secondary {
                color: var(--kbq-foreground-contrast-secondary);
            }
        `,
    ],
})
export class UserEditComponent implements DirtyAware {
    readonly login = input.required<string>();

    private readonly api = inject(ApiService);
    private readonly router = inject(Router);
    private readonly notify = inject(NotifyService);

    protected readonly colors = KbqComponentColors;
    protected readonly alertColors = KbqAlertColors;
    protected readonly types = ACCOUNT_TYPES;
    protected readonly op = new SyncOp();

    protected readonly loading = signal(true);
    protected readonly loadError = signal<unknown>(null);
    protected readonly loadErrorTitle = signal('Не удалось загрузить данные');
    protected readonly user = signal<AdminUser | null>(null);
    private readonly users = signal<AdminUser[]>([]);
    protected readonly tasks = signal<AdminTask[]>([]);
    protected readonly globalLimit = signal<number | null>(null);
    protected readonly hardLimit = signal(10);
    protected readonly access = signal<ReadonlySet<string>>(new Set());

    protected readonly conflict = signal<ConflictInfo | null>(null);
    protected readonly wsJobs = signal<AdminJob[] | null>(null);
    protected readonly saveError = signal<string | null>(null);
    private readonly serverErrors = signal<Partial<Record<'ws' | 'limit' | 'mm', string>>>({});

    protected readonly form = new FormGroup({
        type: new FormControl<AccountType>('user', { nonNullable: true }),
        blocked: new FormControl(false, { nonNullable: true }),
        ws: new FormControl('', { nonNullable: true }),
        limit: new FormControl('', { nonNullable: true }),
        mm: new FormControl('', { nonNullable: true }),
    });
    private readonly value = toSignal(this.form.valueChanges, { initialValue: this.form.getRawValue() });
    protected readonly mmValue = computed(() => this.value().mm ?? '');
    private initial: FormSnapshot | null = null;
    private saved = false;

    constructor() {
        const dirty = inject(DirtyFormService);
        const untrack = dirty.track(() => this.isDirty());
        inject(DestroyRef).onDestroy(untrack);

        effect(() => {
            this.login();
            untracked(() => void this.load());
        });

        // A changed field drops its own server error.
        this.form.controls.ws.valueChanges.subscribe(() => this.clearField('ws'));
        this.form.controls.limit.valueChanges.subscribe(() => this.clearField('limit'));
        this.form.controls.mm.valueChanges.subscribe(() => this.clearField('mm'));
    }

    isDirty(): boolean {
        if (this.saved || !this.initial) return false;
        const now = this.snapshot();
        return (Object.keys(now) as (keyof FormSnapshot)[]).some((k) => now[k] !== this.initial![k]);
    }

    async load(): Promise<void> {
        this.loading.set(true);
        this.loadError.set(null);
        try {
            const [users, tasks, config] = await Promise.all([
                this.api.get<AdminUser[]>('/api/admin/users'),
                this.api.get<AdminTask[]>('/api/admin/tasks'),
                this.api.get<{ max_active_sessions?: number; max_active_jobs_hard_limit?: number }>(
                    '/api/config/public',
                ),
            ]);
            this.users.set(users);
            this.tasks.set(tasks);
            this.globalLimit.set(config.max_active_sessions ?? null);
            if (config.max_active_jobs_hard_limit) this.hardLimit.set(config.max_active_jobs_hard_limit);
            const user = users.find((u) => u.login === this.login()) ?? null;
            if (!user) {
                this.loadErrorTitle.set('Пользователь не найден');
                this.loadError.set(new ApiError(404, null, `Нет пользователя ${this.login()}`, null));
                return;
            }
            this.fill(user);
        } catch (err) {
            this.loadErrorTitle.set('Не удалось загрузить данные');
            this.loadError.set(err);
        } finally {
            this.loading.set(false);
        }
    }

    private fill(user: AdminUser): void {
        this.user.set(user);
        this.form.reset({
            type: accountType(user),
            blocked: isBlocked(user),
            ws: user.workspace_path ?? '',
            limit: user.max_active_jobs ? String(user.max_active_jobs) : '',
            mm: user.mattermost_username ?? '',
        });
        this.access.set(
            new Set(
                this.tasks()
                    .filter((t) => t.visibility !== 'all' && hasTaskAccess(t, user.id))
                    .map((t) => t.id),
            ),
        );
        this.initial = this.snapshot();
    }

    private snapshot(): FormSnapshot {
        const v = this.form.getRawValue();
        return {
            type: v.type,
            blocked: v.blocked,
            ws: v.ws.trim(),
            limit: v.limit.trim(),
            mm: v.mm.trim().replace(/^@/, ''),
            tasks: [...this.access()].sort().join(','),
        };
    }

    protected toggleTask(id: string): void {
        const next = new Set(this.access());
        if (next.has(id)) next.delete(id);
        else next.add(id);
        this.access.set(next);
    }

    protected fieldError(field: 'ws' | 'limit' | 'mm'): string | null {
        return this.serverErrors()[field] ?? null;
    }

    private clearField(field: 'ws' | 'limit' | 'mm'): void {
        if (!this.serverErrors()[field]) return;
        const next = { ...this.serverErrors() };
        delete next[field];
        this.serverErrors.set(next);
    }

    /** Client-side checks; true when the form can be sent. */
    private validate(): boolean {
        const s = this.snapshot();
        const errors: Partial<Record<'ws' | 'limit' | 'mm', string>> = {};
        if (s.type !== 'admin' && !s.ws) errors.ws = 'Укажите рабочую папку';
        if (s.limit && !this.limitOk(s.limit)) errors.limit = `От 1 до ${this.hardLimit()}`;
        this.serverErrors.set(errors);
        return !Object.keys(errors).length;
    }

    private limitOk(raw: string): boolean {
        if (!/^\d+$/.test(raw)) return false;
        const n = Number(raw);
        return n >= 1 && n <= this.hardLimit();
    }

    protected save(): void {
        void this.submit(this.user()?.updated_at ?? null);
    }

    protected overwrite(c: ConflictInfo): void {
        void this.submit(c.updatedAt);
    }

    private async submit(updatedAt: string | null): Promise<void> {
        const user = this.user();
        if (!user || !this.initial || this.op.busy()) return;
        this.conflict.set(null);
        this.wsJobs.set(null);
        this.saveError.set(null);
        if (!this.validate()) return;

        const s = this.snapshot();
        const init = this.initial;
        const body: Record<string, unknown> = {};
        if (s.type !== init.type) Object.assign(body, accountTypeToApi(s.type));
        if (s.blocked !== init.blocked) body['status'] = s.blocked ? 'disabled' : 'active';
        if (s.ws !== init.ws) body['workspace_path'] = s.ws;
        if (s.limit !== init.limit) body['max_active_jobs'] = s.limit ? Number(s.limit) : null;
        if (s.mm !== init.mm) body['mattermost_username'] = s.mm;
        const tasksChanged = s.tasks !== init.tasks;
        const wsChanged = s.ws !== init.ws;
        if (updatedAt) body['updated_at'] = updatedAt;

        const result = await this.op.run(async () => {
            if (Object.keys(body).some((k) => k !== 'updated_at')) {
                await this.api.patch<AdminUser>(`/api/admin/users/${encodeURIComponent(String(user.id))}`, body);
            }
            if (tasksChanged) {
                await this.api.put(`/api/admin/users/${encodeURIComponent(String(user.id))}/task-access`, {
                    task_ids: [...this.access()],
                });
            }
        });
        if (result.ok) {
            this.saved = true;
            await this.router.navigate(['/admin/users'], { queryParams: { focus: user.login } });
            this.notify.success(
                'Настройки сохранены',
                wsChanged ? 'Чаты из старой папки перенесены в архив пользователя' : undefined,
            );
            return;
        }
        if ('error' in result) await this.showError(result.error, user);
    }

    private async showError(err: unknown, user: AdminUser): Promise<void> {
        const conflict = readConflict(err, this.users());
        if (conflict) {
            this.conflict.set(conflict);
            return;
        }
        if (err instanceof ApiError) {
            if (err.isConflict && err.detail === WS_BUSY) {
                this.serverErrors.set({ ws: 'Сейчас нельзя: есть задания в очереди или в работе' });
                this.wsJobs.set(await this.loadJobs(user));
                return;
            }
            if (err.isConflict && err.detail === MM_TAKEN) {
                this.serverErrors.set({ mm: `Mattermost @${this.snapshot().mm} уже привязан к другому пользователю` });
                return;
            }
            if (err.status === 422 && err.detail?.startsWith('Value exceeds')) {
                this.serverErrors.set({ limit: `От 1 до ${this.hardLimit()}` });
                return;
            }
            if (err.status === 422 && err.detail === 'Workspace is required for a user') {
                this.serverErrors.set({ ws: 'Укажите рабочую папку' });
                return;
            }
        }
        this.saveError.set(describeError(err).text);
    }

    private async loadJobs(user: AdminUser): Promise<AdminJob[]> {
        try {
            const queue = await this.api.get<{ jobs: AdminJob[] }>('/api/admin/queue');
            return queue.jobs.filter(
                (j) => String(j.effective_user_id) === String(user.id) && UNFINISHED_JOB_STATUSES.includes(j.status),
            );
        } catch {
            return [];
        }
    }

    protected jobsText(jobs: readonly AdminJob[]): string {
        if (!jobs.length) return '';
        const list = jobs.map(
            (j) => `«${j.task_title || j.chat_title || j.id}» — ${statusLabel(j.status).toLowerCase()}`,
        );
        return `${list.join('; ')}. `;
    }

    protected conflictTitle(c: ConflictInfo): string {
        return conflictText(c);
    }
}
