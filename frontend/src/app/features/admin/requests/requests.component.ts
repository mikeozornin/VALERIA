import { ChangeDetectionStrategy, Component, computed, effect, inject, input, signal, untracked } from '@angular/core';
import { Router } from '@angular/router';
import { KbqBadgeColors, KbqBadgeModule } from '@koobiq/components/badge';
import { KbqButtonModule } from '@koobiq/components/button';
import { KbqComponentColors } from '@koobiq/components/core';
import { KbqEmptyStateModule } from '@koobiq/components/empty-state';
import {
    KbqFilter,
    KbqFilterBarModule,
    KbqPipe,
    KbqPipeTemplate,
    KbqPipeTypes,
    KbqSelectValue,
} from '@koobiq/components/filter-bar';
import { KbqIconModule } from '@koobiq/components/icon';
import { KbqTableModule } from '@koobiq/components/table';
import { ApiService } from '../../../core/api/api.service';
import { matches, tokenize } from '../../../core/search/search';
import { ItemKind, KIND_ICON, adminStatusLabel, statusBadgeColor } from '../../../core/status/status';
import { PageStateComponent } from '../../../shared/page-state/page-state.component';
import { ScreenHeaderComponent } from '../../../shell/screen-header/screen-header.component';
import { AdminRequest, AdminTask } from '../shared/admin.models';
import { AdminSearchComponent } from '../shared/admin-search.component';
import { ADMIN_LIST_STYLES } from '../shared/admin-styles';
import { formatWhen } from '../shared/format';
import {
    listFromQuery,
    listToQuery,
    PERIODS,
    periodFromPipe,
    periodFromQuery,
    periodRange,
    periodToPipe,
    periodToQuery,
} from '../shared/period';
import { KIND_LABEL } from '../shared/requests.helpers';
import { pipeTrackKey } from '../shared/pipe-track';
import { requestFilterParams, RequestFilters } from './requests.filters';

const KINDS: ItemKind[] = ['question', 'task', 'folder'];

/** «Запросы пользователей» (`/admin/requests`, SPEC §10.5): every user's questions, task runs and folder work. */
@Component({
    selector: 'v-admin-requests',
    imports: [
        ScreenHeaderComponent,
        PageStateComponent,
        AdminSearchComponent,
        KbqFilterBarModule,
        KbqTableModule,
        KbqBadgeModule,
        KbqButtonModule,
        KbqIconModule,
        KbqEmptyStateModule,
    ],
    changeDetection: ChangeDetectionStrategy.OnPush,
    template: `
        <v-screen-header title="Запросы пользователей">
            <ng-container vHeaderAfterTitle>
                @if (!loading() && !error()) {
                    <kbq-badge [badgeColor]="badgeColors.FadeContrast">
                        {{ rows().length }}{{ filtered() && total() !== null ? ' из ' + total() : '' }}
                    </kbq-badge>
                }
            </ng-container>
        </v-screen-header>

        <div class="v-content">
            <div class="v-filters">
                <kbq-filter-bar [filter]="filter()" [pipeTemplates]="templates()" (onChangePipe)="onPipe($event)">
                    @for (pipe of filter().pipes; track pipeKey(pipe)) {
                        <ng-container *kbqPipe="pipe" />
                    }
                    @if (filtered()) {
                        <kbq-filter-reset (onResetFilter)="resetAll()" />
                    }
                </kbq-filter-bar>
                <v-admin-search
                    class="v-search"
                    placeholder="Текст запроса или логин"
                    [value]="q()"
                    (valueChange)="setQuery($event)"
                />
            </div>

            <v-page-state [loading]="loading()" [error]="error()" (retry)="load()">
                @if (rows().length) {
                    <table kbq-table [border]="true" class="v-table v-mt8">
                        <thead>
                            <tr>
                                <th>Запрос</th>
                                <th>Пользователь</th>
                                <th>Статус</th>
                                <th>Создан</th>
                            </tr>
                        </thead>
                        <tbody>
                            @for (r of rows(); track r.kind + r.id) {
                                <tr tabindex="0" (click)="open(r)" (keydown.enter)="open(r)">
                                    <td>
                                        <span class="v-title">{{ r.title }}</span>
                                        <span class="v-sub">
                                            <i
                                                kbq-icon
                                                class="v-subic"
                                                [class]="kindIcon(r.kind)"
                                                aria-hidden="true"
                                            ></i>
                                            {{ kindLabel(r.kind) }}{{ r.task_title ? ' · ' + r.task_title : '' }}
                                        </span>
                                    </td>
                                    <td class="v-mono v-nowrap">{{ r.user.login || dash }}</td>
                                    <td>
                                        <kbq-badge [badgeColor]="badgeColor(r)">{{ status(r) }}</kbq-badge>
                                    </td>
                                    <td class="v-secondary v-nowrap">{{ when(r.created_at) }}</td>
                                </tr>
                            }
                        </tbody>
                    </table>
                } @else {
                    <kbq-empty-state class="v-empty">
                        <div kbq-empty-state-title>Запросов не нашли</div>
                        <div kbq-empty-state-text>Ищем по тексту запроса и логину с учётом фильтров.</div>
                        @if (filtered()) {
                            <div kbq-empty-state-actions>
                                <button kbq-button [color]="colors.ContrastFade" (click)="resetAll()">
                                    Сбросить фильтры
                                </button>
                            </div>
                        }
                    </kbq-empty-state>
                }
            </v-page-state>
        </div>
    `,
    styles: [
        ADMIN_LIST_STYLES,
        `
            .v-content {
                flex: 1 1 auto;
                min-height: 0;
                height: auto;
            }
            .v-mt8 {
                margin-top: 8px;
            }
            .v-search {
                width: 240px;
                margin-left: auto;
            }
            .v-title {
                color: var(--kbq-foreground-contrast);
            }
            .v-subic {
                margin-right: 4px;
                vertical-align: -3px;
                color: var(--kbq-icon-contrast-fade);
            }
        `,
    ],
})
export class RequestsComponent {
    readonly user = input<string>();
    readonly period = input<string>();
    readonly from = input<string>();
    readonly to = input<string>();
    readonly type = input<string>();
    readonly task = input<string>();
    readonly q = input<string>('');

    private readonly api = inject(ApiService);
    private readonly router = inject(Router);

    protected readonly colors = KbqComponentColors;
    protected readonly badgeColors = KbqBadgeColors;
    protected readonly dash = '—';
    protected readonly pipeKey = pipeTrackKey;

    protected readonly loading = signal(true);
    protected readonly error = signal<unknown>(null);
    private readonly items = signal<AdminRequest[]>([]);
    /** Unfiltered list: total for «N из M» and the user pipe options. */
    private readonly all = signal<AdminRequest[] | null>(null);
    private readonly tasks = signal<AdminTask[]>([]);
    protected readonly total = computed(() => this.all()?.length ?? null);

    protected readonly state = computed<RequestFilters>(() => ({
        users: listFromQuery(this.user()),
        period: periodFromQuery(this.period(), this.from(), this.to()),
        types: listFromQuery(this.type()).filter((t): t is ItemKind => (KINDS as string[]).includes(t)),
        task: this.task() || null,
    }));
    protected readonly pipesFiltered = computed(() => {
        const s = this.state();
        return !!(s.users.length || s.period || s.types.length || s.task);
    });
    protected readonly filtered = computed(() => this.pipesFiltered() || !!this.q()?.trim());

    protected readonly rows = computed(() => {
        const tokens = tokenize(this.q() ?? '');
        return this.items().filter((r) => matches([r.title, r.user.login, KIND_LABEL[r.kind], r.task_title], tokens));
    });

    private readonly userOptions = computed<KbqSelectValue[]>(() => {
        const logins = new Set<string>(this.state().users);
        for (const r of this.all() ?? []) if (r.user.login) logins.add(r.user.login);
        return [...logins].sort().map((login) => ({ name: login, value: login, id: login }));
    });
    private readonly kindOptions: KbqSelectValue[] = KINDS.map((k) => ({ name: KIND_LABEL[k], value: k, id: k }));
    private readonly taskOptions = computed<KbqSelectValue[]>(() =>
        this.tasks().map((t) => ({ name: t.title, value: t.id, id: t.id })),
    );

    protected readonly templates = computed<KbqPipeTemplate[]>(() => [
        {
            name: 'Пользователь',
            id: 'user',
            type: KbqPipeTypes.MultiSelect,
            values: this.userOptions(),
            search: true,
            cleanable: true,
            removable: false,
            disabled: false,
            compareWith: byValue,
        },
        {
            name: 'Период',
            id: 'period',
            type: KbqPipeTypes.Datetime,
            values: [...PERIODS],
            cleanable: true,
            removable: false,
            disabled: false,
        },
        {
            name: 'Тип',
            id: 'type',
            type: KbqPipeTypes.MultiSelect,
            values: this.kindOptions,
            cleanable: true,
            removable: false,
            disabled: false,
            compareWith: byValue,
        },
        {
            name: 'Задача',
            id: 'task',
            type: KbqPipeTypes.Select,
            values: this.taskOptions(),
            search: true,
            cleanable: true,
            removable: false,
            disabled: false,
            compareWith: byValue,
        },
    ]);

    protected readonly filter = computed<KbqFilter>(() => {
        const s = this.state();
        const users = this.userOptions().filter((o) => s.users.includes(o.value as string));
        const types = this.kindOptions.filter((o) => s.types.includes(o.value as ItemKind));
        const pipes: KbqPipe[] = [
            {
                name: 'Пользователь',
                id: 'user',
                type: KbqPipeTypes.MultiSelect,
                value: users,
                search: true,
                cleanable: true,
                removable: false,
                disabled: false,
            },
            {
                name: 'Период',
                id: 'period',
                type: KbqPipeTypes.Datetime,
                value: periodToPipe(s.period),
                cleanable: true,
                removable: false,
                disabled: false,
            },
            {
                name: 'Тип',
                id: 'type',
                type: KbqPipeTypes.MultiSelect,
                value: types,
                cleanable: true,
                removable: false,
                disabled: false,
            },
        ];
        if (s.types.includes('task')) {
            const task = this.taskOptions().find((o) => o.value === s.task) ?? null;
            pipes.push({
                name: 'Задача',
                id: 'task',
                type: KbqPipeTypes.Select,
                value: task,
                search: true,
                cleanable: true,
                removable: false,
                disabled: false,
            });
        }
        return { name: '', pipes, readonly: false, disabled: false, changed: false, saved: false };
    });

    private loadSeq = 0;

    constructor() {
        void this.loadStatic();
        effect(() => {
            const s = this.state();
            untracked(() => void this.load(s));
        });
    }

    private async loadStatic(): Promise<void> {
        const [all, tasks] = await Promise.allSettled([
            this.api.get<AdminRequest[]>('/api/admin/requests'),
            this.api.get<AdminTask[]>('/api/admin/tasks'),
        ]);
        if (all.status === 'fulfilled') this.all.set(all.value);
        if (tasks.status === 'fulfilled') this.tasks.set(tasks.value);
    }

    async load(state: RequestFilters = this.state()): Promise<void> {
        const seq = ++this.loadSeq;
        this.loading.set(true);
        this.error.set(null);
        try {
            const params = requestFilterParams(state, periodRange(state.period));
            const items = await this.api.get<AdminRequest[]>('/api/admin/requests', { params });
            if (seq === this.loadSeq) this.items.set(items);
        } catch (err) {
            if (seq === this.loadSeq) this.error.set(err);
        } finally {
            if (seq === this.loadSeq) this.loading.set(false);
        }
    }

    protected onPipe(pipe: KbqPipe): void {
        const s = this.state();
        const values = (v: unknown): string[] =>
            Array.isArray(v) ? v.map((o: KbqSelectValue) => String(o.value)) : [];
        if (pipe.id === 'user') this.navigate({ ...s, users: values(pipe.value) });
        else if (pipe.id === 'period') this.navigate({ ...s, period: periodFromPipe(pipe.value) });
        else if (pipe.id === 'type') {
            const types = values(pipe.value) as ItemKind[];
            this.navigate({ ...s, types, task: types.includes('task') ? s.task : null });
        } else if (pipe.id === 'task') {
            const v = pipe.value as KbqSelectValue | null;
            this.navigate({ ...s, task: v ? String(v.value) : null });
        }
    }

    private navigate(s: RequestFilters, extra: Record<string, string | null> = {}): void {
        void this.router.navigate([], {
            queryParams: {
                user: listToQuery(s.users),
                ...periodToQuery(s.period),
                type: listToQuery(s.types),
                task: s.task,
                ...extra,
            },
            queryParamsHandling: 'merge',
        });
    }

    protected resetAll(): void {
        this.navigate({ users: [], period: null, types: [], task: null }, { q: null });
    }

    protected setQuery(q: string): void {
        void this.router.navigate([], {
            queryParams: { q: q || null },
            queryParamsHandling: 'merge',
            replaceUrl: true,
        });
    }

    protected open(r: AdminRequest): void {
        void this.router.navigate(['/admin/requests', r.id]);
    }

    protected kindIcon(kind: ItemKind): string {
        return KIND_ICON[kind] ?? KIND_ICON.question;
    }

    protected kindLabel(kind: ItemKind): string {
        return KIND_LABEL[kind] ?? kind;
    }

    protected status(r: AdminRequest): string {
        return adminStatusLabel(r.status, r.kind);
    }

    protected badgeColor(r: AdminRequest): KbqBadgeColors {
        return statusBadgeColor(r.status, r.kind);
    }

    protected when(iso: string): string {
        return formatWhen(iso);
    }
}

function byValue(a: KbqSelectValue | null | undefined, b: KbqSelectValue | null | undefined): boolean {
    return !!a && !!b && a.value === b.value;
}
