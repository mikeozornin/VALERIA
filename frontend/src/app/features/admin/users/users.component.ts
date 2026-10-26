import {
    ChangeDetectionStrategy,
    Component,
    computed,
    DestroyRef,
    effect,
    ElementRef,
    inject,
    input,
    signal,
    TemplateRef,
    untracked,
    viewChild,
} from '@angular/core';
import { FormControl, ReactiveFormsModule, Validators } from '@angular/forms';
import { Router, RouterLink } from '@angular/router';
import { KbqActionsPanel, KbqActionsPanelModule, KbqActionsPanelRef } from '@koobiq/components/actions-panel';
import { KbqAlertColors, KbqAlertModule } from '@koobiq/components/alert';
import { KbqBadgeColors, KbqBadgeModule } from '@koobiq/components/badge';
import { KbqButtonModule } from '@koobiq/components/button';
import { KbqCheckboxModule } from '@koobiq/components/checkbox';
import { KbqComponentColors, PopUpPlacements } from '@koobiq/components/core';
import { KbqContentPanelModule } from '@koobiq/components/content-panel';
import { KbqDividerModule } from '@koobiq/components/divider';
import { KbqDlModule } from '@koobiq/components/dl';
import { KbqEmptyStateModule } from '@koobiq/components/empty-state';
import { KbqFormFieldModule } from '@koobiq/components/form-field';
import { KbqIconModule } from '@koobiq/components/icon';
import { KbqInputModule } from '@koobiq/components/input';
import { KbqModalRef, KbqModalService, ModalSize } from '@koobiq/components/modal';
import { KbqTableModule } from '@koobiq/components/table';
import { KbqToolTipModule } from '@koobiq/components/tooltip';
import { isAccessChanged } from '../../../core/api/api-error';
import { ApiService } from '../../../core/api/api.service';
import { AuthService } from '../../../core/auth/auth.service';
import { NotifyService } from '../../../core/notify/notify.service';
import { matches, tokenize } from '../../../core/search/search';
import { describeError } from '../../../core/status/error-codes';
import { adminStatusLabel, KIND_ICON } from '../../../core/status/status';
import { PageStateComponent } from '../../../shared/page-state/page-state.component';
import { cpanelWidth, CpanelWidthDirective } from '../../../shell/aside/panel-width';
import { ScreenHeaderComponent } from '../../../shell/screen-header/screen-header.component';
import {
    accountType,
    accountTypeLabel,
    AdminRequest,
    AdminTask,
    AdminUser,
    byLastLogin,
    hasTaskAccess,
    isBlocked,
} from '../shared/admin.models';
import { AdminSearchComponent } from '../shared/admin-search.component';
import { ADMIN_LIST_STYLES } from '../shared/admin-styles';
import { BatchAction, batchToast, BatchResult } from '../shared/batch';
import { DASH, formatWhen, plural } from '../shared/format';
import { escapeHtml } from '../config-shared/format';
import { statusTextColor } from '../shared/requests.helpers';
import { SyncOp, SyncSlowAlertComponent } from '../shared/sync-op';
import { OutlineIconComponent } from '../../../shared/outline-icon/outline-icon.component';

const RECENT_LIMIT = 20;

/**
 * «Пользователи» (`/admin/users`, SPEC §10.2): table with checkboxes, the focused row's card in a content panel
 * (`?focus=login`), `kbq-actions-panel` for block / unblock, «Сбросить пароль» modal. Focus and checks are independent.
 */
/** Koobiq renders a string `kbqTitle` through `[innerHTML]`, so the login is escaped. */
export function resetPasswordTitle(login: string): string {
    return `Сбросить пароль ${escapeHtml(login)}?`;
}

@Component({
    selector: 'v-admin-users',
    imports: [
        ReactiveFormsModule,
        RouterLink,
        ScreenHeaderComponent,
        PageStateComponent,
        AdminSearchComponent,
        SyncSlowAlertComponent,
        KbqTableModule,
        KbqCheckboxModule,
        KbqBadgeModule,
        KbqButtonModule,
        KbqIconModule,
        KbqToolTipModule,
        KbqContentPanelModule,
        CpanelWidthDirective,
        KbqDlModule,
        KbqEmptyStateModule,
        KbqActionsPanelModule,
        KbqDividerModule,
        KbqFormFieldModule,
        KbqInputModule,
        KbqAlertModule,
        OutlineIconComponent,
    ],
    providers: [KbqActionsPanel],
    changeDetection: ChangeDetectionStrategy.OnPush,
    template: `
        <v-screen-header title="Пользователи">
            <ng-container vHeaderAfterTitle>
                @if (users().length) {
                    <kbq-badge [badgeColor]="badgeColors.FadeContrast">{{ users().length }}</kbq-badge>
                }
            </ng-container>
            <ng-container vHeaderActions>
                <a kbq-button [color]="colors.Theme" routerLink="/admin/users/new">
                    <i kbq-icon="kbq-plus_16"></i>
                    Создать пользователя
                </a>
            </ng-container>
        </v-screen-header>

        <v-page-state class="v-body" [loading]="loading()" [error]="error()" (retry)="load()">
            <kbq-content-panel-container
                [opened]="!!focused()"
                [width]="panel.width()"
                [minWidth]="panel.min"
                [maxWidth]="panel.max()"
                (openedChange)="onPanelOpened($event)"
            >
                <div class="v-content" #content>
                    <div class="v-filters">
                        <v-admin-search
                            placeholder="Логин, папка или Mattermost"
                            [value]="q()"
                            (valueChange)="setQuery($event)"
                        />
                    </div>
                    @if (rows().length) {
                        <table kbq-table class="v-table v-mt16">
                            <thead>
                                <tr>
                                    <th class="v-check">
                                        <kbq-checkbox
                                            [checked]="allChecked()"
                                            [indeterminate]="someChecked()"
                                            aria-label="Отметить всех"
                                            (change)="toggleAll()"
                                        />
                                    </th>
                                    <th>Пользователь</th>
                                    <th>Последний вход</th>
                                </tr>
                            </thead>
                            <tbody>
                                @for (u of rows(); track u.id) {
                                    <tr
                                        tabindex="0"
                                        [class.v-selected]="u.login === focus()"
                                        [attr.aria-selected]="u.login === focus()"
                                        (click)="openCard(u.login)"
                                        (keydown.enter)="openCard(u.login)"
                                    >
                                        <td class="v-check" (click)="$event.stopPropagation()">
                                            <kbq-checkbox
                                                [checked]="checked().has(u.login)"
                                                [attr.aria-label]="'Отметить ' + u.login"
                                                (change)="toggle(u.login)"
                                            />
                                        </td>
                                        <td class="v-nowrap">
                                            <span class="v-mono">{{ u.login }}</span>
                                            @if (blocked(u)) {
                                                <kbq-badge
                                                    class="v-row-badge"
                                                    [compact]="true"
                                                    [badgeColor]="badgeColors.FadeError"
                                                    >Заблокирован</kbq-badge
                                                >
                                            }
                                            @if (u.login === me()) {
                                                <span class="v-tertiary"> · вы</span>
                                            }
                                            <span class="v-sub">{{ typeLabel(u) }}</span>
                                        </td>
                                        <td class="v-secondary v-nowrap">{{ when(u.last_login_at) }}</td>
                                    </tr>
                                }
                            </tbody>
                        </table>
                    } @else if (users().length) {
                        <kbq-empty-state class="v-empty">
                            <div kbq-empty-state-title>Никого не нашли</div>
                            <div kbq-empty-state-text>Ищем по логину, типу учётки, папке и Mattermost.</div>
                        </kbq-empty-state>
                    }
                </div>

                <kbq-content-panel>
                    @if (focused(); as u) {
                        <kbq-content-panel-header [vCpanelWidth]="panel">
                            <div kbqContentPanelHeaderTitle class="v-panel-title v-mono">{{ u.login }}</div>
                        </kbq-content-panel-header>
                    }
                    @if (focused(); as u) {
                        <kbq-content-panel-body>
                            <div class="v-panel-acts">
                                <a
                                    kbq-button
                                    [color]="colors.ContrastFade"
                                    [routerLink]="['/admin/users', u.login, 'edit']"
                                >
                                    <i kbq-icon="kbq-pencil_16"></i>
                                    Изменить
                                </a>
                                <button kbq-button [color]="colors.ContrastFade" (click)="openReset(u)">
                                    <i kbq-icon="kbq-lock_16"></i>
                                    Сбросить пароль
                                </button>
                            </div>
                            <kbq-dl class="v-dl" [vertical]="false">
                                <kbq-dt>Тип учётки</kbq-dt>
                                <kbq-dd>{{ typeLabel(u) }}</kbq-dd>
                                <kbq-dt>Статус</kbq-dt>
                                <kbq-dd>
                                    @if (blocked(u)) {
                                        <kbq-badge [badgeColor]="badgeColors.FadeError">Заблокирован</kbq-badge>
                                    } @else {
                                        <kbq-badge [badgeColor]="badgeColors.FadeSuccess">Активен</kbq-badge>
                                    }
                                </kbq-dd>
                                <kbq-dt>Рабочая папка</kbq-dt>
                                <kbq-dd class="v-break"
                                    ><span class="v-mono">{{ u.workspace_path || dash }}</span></kbq-dd
                                >
                                <kbq-dt>Mattermost</kbq-dt>
                                <kbq-dd>
                                    @if (u.mattermost_username) {
                                        &#64;{{ u.mattermost_username }}
                                    } @else {
                                        <span class="v-tertiary">не настроен</span>
                                    }
                                </kbq-dd>
                                <kbq-dt>Лимит задач</kbq-dt>
                                <kbq-dd>
                                    @if (u.max_active_jobs) {
                                        {{ u.max_active_jobs }}
                                    } @else {
                                        <span class="v-secondary">общий</span>
                                    }
                                </kbq-dd>
                                <kbq-dt>Доступ к задачам</kbq-dt>
                                <kbq-dd>{{ accessCount(u) }} из {{ tasks().length }}</kbq-dd>
                                <kbq-dt>Последний вход</kbq-dt>
                                <kbq-dd>{{ when(u.last_login_at) }}</kbq-dd>
                                <kbq-dt>Создан</kbq-dt>
                                <kbq-dd>{{ when(u.created_at) }}</kbq-dd>
                            </kbq-dl>

                            <section class="v-csec" aria-label="Запросы пользователя">
                                <div class="v-csec__title v-csec__title--row">
                                    <span>Последние запросы</span>
                                    @if (recent()?.length) {
                                        <a
                                            kbq-button
                                            class="v-csec__more"
                                            kbqStyle="transparent"
                                            [color]="colors.Contrast"
                                            routerLink="/admin/requests"
                                            [queryParams]="{ user: u.login }"
                                        >
                                            Все запросы {{ u.login }}
                                            <i kbq-icon="kbq-arrow-right_16"></i>
                                        </a>
                                    }
                                </div>
                                @if (recent(); as list) {
                                    @for (r of list.slice(0, recentLimit); track r.kind + r.id) {
                                        <a class="v-crow" [routerLink]="['/admin/requests', r.id]">
                                            <i
                                                kbq-icon
                                                [class]="kindIcon(r.kind)"
                                                [color]="colors.ContrastFade"
                                                aria-hidden="true"
                                            ></i>
                                            <span class="v-crow__t">{{ r.title }}</span>
                                            <span class="v-crow__st" [style.color]="statusColor(r)">{{
                                                status(r)
                                            }}</span>
                                            <span class="v-tertiary v-nowrap">{{ when(r.updated_at) }}</span>
                                        </a>
                                    } @empty {
                                        <div class="v-secondary">Запросов пока нет</div>
                                    }
                                } @else if (recentError()) {
                                    <div class="v-secondary">Не удалось загрузить запросы</div>
                                }
                            </section>
                        </kbq-content-panel-body>
                    }
                </kbq-content-panel>
            </kbq-content-panel-container>
        </v-page-state>

        <ng-template #actionsTpl>
            <div class="v-apanel" role="toolbar" aria-label="Действия с отмеченными">
                <span class="v-apanel__count">Выбрано: {{ checkedRows().length }}</span>
                <kbq-divider class="v-apanel__sep" [vertical]="true" />
                <span
                    [kbqTooltip]="'Все выбранные уже заблокированы'"
                    [kbqTooltipDisabled]="!!blockTargets().length"
                    [kbqPlacement]="placements.Top"
                >
                    <button
                        kbq-button
                        [color]="colors.Contrast"
                        [disabled]="!blockTargets().length || batchBusy()"
                        (click)="openBlock()"
                    >
                        <i kbq-icon="kbq-lock_16"></i>
                        Заблокировать
                    </button>
                </span>
                <span
                    [kbqTooltip]="'Среди выбранных нет заблокированных'"
                    [kbqTooltipDisabled]="!!unblockTargets().length"
                    [kbqPlacement]="placements.Top"
                >
                    <button
                        kbq-button
                        [color]="colors.Contrast"
                        [disabled]="!unblockTargets().length || batchBusy()"
                        (click)="runBatch('unblock')"
                    >
                        Разблокировать
                    </button>
                </span>
            </div>
        </ng-template>

        <ng-template #blockTpl>
            <p class="v-modal-p">
                <span class="v-mono">{{ blockLogins().join(', ') }}</span>
                не смогут войти в VALERIA, их открытые сессии завершатся. Чаты и запуски сохранятся, разблокировать
                можно в любой момент.
            </p>
            @if (checkedIncludesMe()) {
                <p class="v-modal-p v-mt8 v-secondary">Вашу учётку {{ me() }} блокировка не затронет.</p>
            }
            @if (alreadyBlocked().length) {
                <p class="v-modal-p v-mt8 v-secondary">Уже заблокированы: {{ alreadyBlocked().join(', ') }}.</p>
            }
        </ng-template>
        <ng-template #blockFooterTpl>
            <button
                kbq-button
                [color]="colors.Error"
                [disabled]="batchBusy()"
                [class.kbq-progress]="batchBusy()"
                (click)="confirmBlock()"
            >
                Заблокировать
            </button>
            <button kbq-button [disabled]="batchBusy()" (click)="modalRef?.close()">Отмена</button>
        </ng-template>

        <ng-template #resetTpl>
            <form class="v-reset" (ngSubmit)="confirmReset()" id="v-reset-form">
                <v-sync-slow-alert [op]="resetOp" />
                @if (resetError(); as e) {
                    <kbq-alert [alertColor]="alertColors.Error" alertStyle="colored">
                        <i kbq-icon="" color="error"><v-outline-icon name="alert-circle" /></i>
                        <div kbq-alert-title>Не удалось сбросить пароль</div>
                        {{ e }}
                    </kbq-alert>
                }
                <kbq-form-field>
                    <kbq-label>Новый временный пароль</kbq-label>
                    <input
                        kbqInput
                        type="text"
                        spellcheck="false"
                        [formControl]="resetPassword"
                        placeholder="Не менее 12 символов"
                        autocomplete="new-password"
                        cdkFocusInitial
                    />
                    <kbq-hint>VALERIA его никуда не отправляет, передайте пароль пользователю сами.</kbq-hint>
                </kbq-form-field>
                <p class="v-modal-p v-mt16">
                    Все открытые сессии {{ resetLogin() }} завершатся. Войти можно будет только с новым паролем.
                </p>
            </form>
        </ng-template>
        <ng-template #resetFooterTpl>
            <span
                [kbqTooltip]="'Пароль не короче 12 символов'"
                [kbqTooltipDisabled]="resetValid()"
                [kbqPlacement]="placements.Top"
            >
                <button
                    kbq-button
                    type="submit"
                    form="v-reset-form"
                    [color]="colors.Theme"
                    [disabled]="!resetValid() || resetOp.busy()"
                    [class.kbq-progress]="resetOp.busy()"
                >
                    Сбросить пароль
                </button>
            </span>
            <button kbq-button [disabled]="resetOp.busy()" (click)="modalRef?.close()">Отмена</button>
        </ng-template>
    `,
    styles: [
        ADMIN_LIST_STYLES,
        `
            .v-mt16 {
                margin-top: 16px;
            }
            .v-mt8 {
                margin-top: 8px;
            }
            .v-row-badge {
                margin-left: 4px;
                vertical-align: 1px;
            }
            .v-crow {
                display: grid;
                grid-template-columns: 16px minmax(0, 1fr) auto auto;
                gap: 8px;
                align-items: center;
                padding: 6px 8px;
                margin: 0 -8px;
                border-radius: 8px;
                color: inherit;
                text-decoration: none;
            }
            .v-crow:hover {
                background: var(--kbq-states-background-transparent-hover);
            }
            .v-crow:focus-visible {
                outline: 2px solid var(--kbq-states-line-focus-theme);
                outline-offset: -2px;
            }
            .v-crow__t {
                overflow: hidden;
                text-overflow: ellipsis;
                white-space: nowrap;
            }
            .v-crow__st {
                font-size: 12px;
                white-space: nowrap;
            }
            .v-nowrap {
                white-space: nowrap;
            }
            .v-apanel {
                display: flex;
                align-items: center;
                gap: 8px;
                white-space: nowrap;
            }
            .v-apanel__count {
                font-weight: 500;
                padding: 0 8px;
            }
            .v-apanel__sep {
                height: 20px;
            }
            .v-modal-p {
                margin: 0;
            }
            .v-reset {
                display: flex;
                flex-direction: column;
                gap: 16px;
            }
            .v-reset .v-modal-p.v-mt16 {
                margin-top: 0;
            }
        `,
    ],
})
export class UsersComponent {
    /** `?focus=login`: row whose card is open. */
    readonly focus = input<string | undefined>();
    /** `?q=`: search query. */
    readonly q = input<string>('');

    private readonly api = inject(ApiService);
    private readonly auth = inject(AuthService);
    private readonly router = inject(Router);
    private readonly notify = inject(NotifyService);
    private readonly modal = inject(KbqModalService);
    private readonly actionsPanel = inject(KbqActionsPanel, { self: true });

    protected readonly colors = KbqComponentColors;
    protected readonly badgeColors = KbqBadgeColors;
    protected readonly alertColors = KbqAlertColors;
    protected readonly placements = PopUpPlacements;
    protected readonly dash = DASH;
    protected readonly recentLimit = RECENT_LIMIT;
    protected readonly panel = cpanelWidth('users');

    protected readonly loading = signal(true);
    protected readonly error = signal<unknown>(null);
    protected readonly users = signal<AdminUser[]>([]);
    protected readonly tasks = signal<AdminTask[]>([]);
    protected readonly checked = signal<ReadonlySet<string>>(new Set());
    protected readonly recent = signal<AdminRequest[] | null>(null);
    protected readonly recentError = signal(false);
    protected readonly batchBusy = signal(false);

    protected readonly me = computed(() => this.auth.user()?.login ?? '');
    protected readonly rows = computed(() => {
        const tokens = tokenize(this.q() ?? '');
        return this.users()
            .filter((u) =>
                matches([u.login, accountTypeLabel(accountType(u)), u.workspace_path, u.mattermost_username], tokens),
            )
            .sort(byLastLogin);
    });
    protected readonly focused = computed(() => this.users().find((u) => u.login === this.focus()) ?? null);
    protected readonly checkedRows = computed(() => this.rows().filter((u) => this.checked().has(u.login)));
    protected readonly allChecked = computed(
        () => !!this.rows().length && this.checkedRows().length === this.rows().length,
    );
    protected readonly someChecked = computed(() => !!this.checkedRows().length && !this.allChecked());
    /** Checked, active, not me: who «Заблокировать» affects. */
    protected readonly blockTargets = computed(() =>
        this.checkedRows().filter((u) => !isBlocked(u) && u.login !== this.me()),
    );
    protected readonly unblockTargets = computed(() => this.checkedRows().filter((u) => isBlocked(u)));
    protected readonly blockLogins = computed(() => this.blockTargets().map((u) => u.login));
    protected readonly alreadyBlocked = computed(() => this.unblockTargets().map((u) => u.login));
    protected readonly checkedIncludesMe = computed(() => this.checkedRows().some((u) => u.login === this.me()));

    protected readonly resetPassword = new FormControl('', {
        nonNullable: true,
        validators: [Validators.minLength(12)],
    });
    private readonly resetValue = signal('');
    protected readonly resetValid = computed(() => this.resetValue().length >= 12);
    protected readonly resetLogin = signal('');
    protected readonly resetError = signal<string | null>(null);
    protected readonly resetOp = new SyncOp();
    protected modalRef: KbqModalRef | null = null;

    private readonly content = viewChild<ElementRef<HTMLElement>>('content');
    private readonly actionsTpl = viewChild.required<TemplateRef<unknown>>('actionsTpl');
    private readonly blockTpl = viewChild.required<TemplateRef<object>>('blockTpl');
    private readonly blockFooterTpl = viewChild.required<TemplateRef<object>>('blockFooterTpl');
    private readonly resetTpl = viewChild.required<TemplateRef<object>>('resetTpl');
    private readonly resetFooterTpl = viewChild.required<TemplateRef<object>>('resetFooterTpl');
    private actionsRef: KbqActionsPanelRef | null = null;
    private recentFor: string | null = null;

    constructor() {
        void this.load();
        this.resetPassword.valueChanges.subscribe((v) => this.resetValue.set(v));

        // Actions panel follows the checked rows.
        effect(() => {
            const count = this.checkedRows().length;
            untracked(() => this.syncActionsPanel(count));
        });

        // Recent requests of the focused user.
        effect(() => {
            const login = this.focused()?.login ?? null;
            untracked(() => void this.loadRecent(login));
        });

        inject(DestroyRef).onDestroy(() => {
            this.actionsRef?.close();
            this.modalRef?.close();
        });
    }

    async load(): Promise<void> {
        this.loading.set(true);
        this.error.set(null);
        try {
            const [users, tasks] = await Promise.all([
                this.api.get<AdminUser[]>('/api/admin/users'),
                this.api.get<AdminTask[]>('/api/admin/tasks'),
            ]);
            this.users.set(users);
            this.tasks.set(tasks);
            this.dropMissingChecks();
        } catch (err) {
            this.error.set(err);
        } finally {
            this.loading.set(false);
        }
    }

    private async reloadUsers(): Promise<void> {
        try {
            this.users.set(await this.api.get<AdminUser[]>('/api/admin/users'));
            this.dropMissingChecks();
        } catch {
            // The list stays as it was; the toast already told the result.
        }
    }

    private dropMissingChecks(): void {
        const logins = new Set(this.users().map((u) => u.login));
        const kept = [...this.checked()].filter((l) => logins.has(l));
        if (kept.length !== this.checked().size) this.checked.set(new Set(kept));
    }

    private async loadRecent(login: string | null): Promise<void> {
        this.recentFor = login;
        this.recent.set(null);
        this.recentError.set(false);
        if (!login) return;
        try {
            const list = await this.api.get<AdminRequest[]>('/api/admin/requests', { params: { user: login } });
            if (this.recentFor === login) this.recent.set(list);
        } catch {
            if (this.recentFor === login) this.recentError.set(true);
        }
    }

    protected setQuery(q: string): void {
        void this.router.navigate([], {
            queryParams: { q: q || null },
            queryParamsHandling: 'merge',
            replaceUrl: true,
        });
    }

    protected openCard(login: string): void {
        void this.router.navigate([], { queryParams: { focus: login }, queryParamsHandling: 'merge' });
    }

    protected onPanelOpened(opened: boolean): void {
        if (!opened && this.focus()) {
            void this.router.navigate([], { queryParams: { focus: null }, queryParamsHandling: 'merge' });
        }
    }

    protected toggle(login: string): void {
        const next = new Set(this.checked());
        if (next.has(login)) next.delete(login);
        else next.add(login);
        this.checked.set(next);
    }

    protected toggleAll(): void {
        const rows = this.rows().map((u) => u.login);
        const next = new Set(this.checked());
        if (this.allChecked()) rows.forEach((l) => next.delete(l));
        else rows.forEach((l) => next.add(l));
        this.checked.set(next);
    }

    private syncActionsPanel(count: number): void {
        if (count && !this.actionsRef) {
            const container = this.content();
            this.actionsRef = this.actionsPanel.open(this.actionsTpl(), {
                overlayContainer: container,
                closeOnNavigation: true,
            });
            const ref = this.actionsRef;
            ref.afterClosed.subscribe(() => {
                if (this.actionsRef !== ref) return;
                this.actionsRef = null;
                // Closed by the user (✕ or Esc) while rows are still checked: uncheck them.
                if (this.checkedRows().length) this.checked.set(new Set());
            });
        } else if (!count && this.actionsRef) {
            const ref = this.actionsRef;
            this.actionsRef = null;
            ref.close();
        }
    }

    protected openBlock(): void {
        const n = this.blockTargets().length;
        if (!n) return;
        this.modalRef = this.modal.create({
            kbqTitle: `Заблокировать ${n} ${plural(n, 'пользователя', 'пользователей', 'пользователей')}?`,
            kbqContent: this.blockTpl(),
            kbqFooter: this.blockFooterTpl(),
            kbqSize: ModalSize.Small,
            kbqClosable: true,
            kbqCloseByESC: true,
        });
        const ref = this.modalRef;
        ref.afterClose.subscribe(() => {
            if (this.modalRef === ref) this.modalRef = null;
        });
    }

    protected async confirmBlock(): Promise<void> {
        await this.runBatch('block');
        this.modalRef?.close();
    }

    protected async runBatch(action: BatchAction): Promise<void> {
        const targets = action === 'block' ? this.blockTargets() : this.unblockTargets();
        if (!targets.length || this.batchBusy()) return;
        this.batchBusy.set(true);
        try {
            const res = await this.api.post<{ results: BatchResult[] }>('/api/admin/users/batch', {
                ids: targets.map((u) => u.id),
                status: action === 'block' ? 'blocked' : 'active',
            });
            const toast = batchToast(action, res.results);
            if (toast) this.notify[toast.kind](toast.title);
        } catch (err) {
            this.notify.error(
                action === 'block' ? 'Не удалось заблокировать' : 'Не удалось разблокировать',
                describeError(err).text,
                err,
            );
        } finally {
            this.batchBusy.set(false);
        }
        await this.reloadUsers();
    }

    protected openReset(user: AdminUser): void {
        this.resetLogin.set(user.login);
        this.resetPassword.reset('');
        this.resetError.set(null);
        this.resetTarget = user;
        this.modalRef = this.modal.create({
            kbqTitle: resetPasswordTitle(user.login),
            kbqContent: this.resetTpl(),
            kbqFooter: this.resetFooterTpl(),
            kbqSize: ModalSize.Small,
            kbqClosable: true,
            kbqCloseByESC: true,
        });
        const ref = this.modalRef;
        ref.afterClose.subscribe(() => {
            if (this.modalRef === ref) this.modalRef = null;
            if (this.resetOp.busy()) this.resetOp.cancel();
        });
    }

    private resetTarget: AdminUser | null = null;

    protected async confirmReset(): Promise<void> {
        const user = this.resetTarget;
        if (!user || !this.resetValid() || this.resetOp.busy()) return;
        this.resetError.set(null);
        const result = await this.resetOp.run(() =>
            this.api.post<void>(`/api/admin/users/${encodeURIComponent(String(user.id))}/reset-password`, {
                password: this.resetPassword.value,
            }),
        );
        if (result.ok) {
            this.modalRef?.close();
            this.notify.success('Пароль сброшен', 'Открытые сессии пользователя завершены');
        } else if ('error' in result && !isAccessChanged(result.error)) {
            this.resetError.set(describeError(result.error).text);
        }
    }

    protected blocked(u: AdminUser): boolean {
        return isBlocked(u);
    }

    protected typeLabel(u: AdminUser): string {
        return accountTypeLabel(accountType(u));
    }

    protected accessCount(u: AdminUser): number {
        return this.tasks().filter((t) => hasTaskAccess(t, u.id)).length;
    }

    protected when(iso: string | null | undefined): string {
        return formatWhen(iso);
    }

    protected kindIcon(kind: AdminRequest['kind']): string {
        return KIND_ICON[kind] ?? KIND_ICON.question;
    }

    protected status(r: AdminRequest): string {
        return adminStatusLabel(r.status, r.kind);
    }

    protected statusColor(r: AdminRequest): string {
        return statusTextColor(r.status, r.kind);
    }
}
