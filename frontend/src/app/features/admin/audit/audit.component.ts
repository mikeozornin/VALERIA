import { ChangeDetectionStrategy, Component, computed, effect, inject, input, signal, untracked } from '@angular/core';
import { Router } from '@angular/router';
import { KbqButtonModule } from '@koobiq/components/button';
import {
    KbqCodeBlockFile,
    KbqCodeBlockModule,
    kbqCodeBlockHighlightJsConfigProvider,
} from '@koobiq/components/code-block';
import { KbqComponentColors } from '@koobiq/components/core';
import { KbqContentPanelModule } from '@koobiq/components/content-panel';
import { KbqDlModule } from '@koobiq/components/dl';
import { KbqEmptyStateModule } from '@koobiq/components/empty-state';
import {
    KbqFilter,
    KbqFilterBarModule,
    KbqPipe,
    KbqPipeTemplate,
    KbqPipeTypes,
    KbqSelectValue,
} from '@koobiq/components/filter-bar';
import { KbqProgressSpinnerModule } from '@koobiq/components/progress-spinner';
import { KbqTableModule } from '@koobiq/components/table';
import { ApiService } from '../../../core/api/api.service';
import { matches, tokenize } from '../../../core/search/search';
import { PageStateComponent } from '../../../shared/page-state/page-state.component';
import { cpanelWidth, CpanelWidthDirective } from '../../../shell/aside/panel-width';
import { ScreenHeaderComponent } from '../../../shell/screen-header/screen-header.component';
import { AdminRequest, AdminTask, AdminUser } from '../shared/admin.models';
import { AdminSearchComponent } from '../shared/admin-search.component';
import { ADMIN_LIST_STYLES } from '../shared/admin-styles';
import { AUDIT_CATEGORIES, AuditCategory, auditLabel } from '../shared/audit-labels';
import { DASH, formatWhen } from '../shared/format';
import { PERIODS, periodFromPipe, periodRange, periodToPipe } from '../shared/period';
import {
    auditApiParams,
    AuditEvent,
    auditFiltered,
    auditFiltersFromQuery,
    auditFiltersToQuery,
    AuditFilters,
    auditMetadata,
    AuditObjectContext,
    auditObjectLabel,
} from './audit.filters';

const PAGE_SIZE = 100;
/** While searching or looking for a deep-linked event, pages are fetched up to this many events. */
const AUTO_LOAD_LIMIT = 2000;

interface AuditRow extends AuditEvent {
    key: string;
    label: string;
    object: string;
}

/** «Аудит» (`/admin/audit`, SPEC §10.6): who did what, with details of an event in the side panel. */
@Component({
    selector: 'v-admin-audit',
    imports: [
        ScreenHeaderComponent,
        PageStateComponent,
        AdminSearchComponent,
        CpanelWidthDirective,
        KbqFilterBarModule,
        KbqTableModule,
        KbqButtonModule,
        KbqEmptyStateModule,
        KbqContentPanelModule,
        KbqDlModule,
        KbqCodeBlockModule,
        KbqProgressSpinnerModule,
    ],
    providers: [
        kbqCodeBlockHighlightJsConfigProvider({
            core: () => import('highlight.js/lib/core'),
            languages: { json: () => import('highlight.js/lib/languages/json') },
        }),
    ],
    changeDetection: ChangeDetectionStrategy.OnPush,
    template: `
        <v-screen-header title="Аудит" />

        <div class="v-body">
            <kbq-content-panel-container
                [opened]="!!selected()"
                [width]="panel.width()"
                [minWidth]="panel.min"
                [maxWidth]="panel.max"
                (openedChange)="onPanelOpened($event)"
            >
                <div class="v-content" (scroll)="onScroll($event)">
                    <div class="v-filters">
                        <kbq-filter-bar
                            [filter]="filter()"
                            [pipeTemplates]="templates()"
                            (onChangePipe)="onPipe($event)"
                        >
                            @for (pipe of filter().pipes; track pipe.id) {
                                <ng-container *kbqPipe="pipe" />
                            }
                            @if (pipesFiltered()) {
                                <kbq-filter-reset (onResetFilter)="resetFilters()" />
                            }
                        </kbq-filter-bar>
                        <v-admin-search
                            class="v-search"
                            placeholder="Действие, логин или объект"
                            [value]="q()"
                            (valueChange)="setQuery($event)"
                        />
                    </div>

                    <v-page-state class="v-mt8" [loading]="loading()" [error]="error()" (retry)="reload()">
                        @if (rows().length) {
                            <table kbq-table class="v-table">
                                <thead>
                                    <tr>
                                        <th>Время</th>
                                        <th>Кто</th>
                                        <th>Действие</th>
                                        @if (!selected()) {
                                            <th>Объект</th>
                                        }
                                    </tr>
                                </thead>
                                <tbody>
                                    @for (a of rows(); track a.key) {
                                        <tr
                                            tabindex="0"
                                            [class.v-selected]="a.key === event()"
                                            [attr.aria-selected]="a.key === event()"
                                            (click)="openEvent(a)"
                                            (keydown.enter)="openEvent(a)"
                                        >
                                            <td class="v-secondary v-nowrap">{{ when(a.created_at) }}</td>
                                            <td class="v-nowrap">
                                                <span class="v-mono">{{ a.actor_login || dash }}</span>
                                                @if (a.effective_login && a.effective_login !== a.actor_login) {
                                                    <span class="v-sub">за {{ a.effective_login }}</span>
                                                }
                                            </td>
                                            @if (selected()) {
                                                <td>
                                                    {{ a.label }}<span class="v-sub">{{ a.object }}</span>
                                                </td>
                                            } @else {
                                                <td>
                                                    {{ a.label }}<span class="v-sub v-mono">{{ a.action }}</span>
                                                </td>
                                                <td>{{ a.object }}</td>
                                            }
                                        </tr>
                                    }
                                </tbody>
                            </table>
                            @if (loadingMore()) {
                                <div class="v-more"><kbq-progress-spinner mode="indeterminate" size="compact" /></div>
                            }
                        } @else if (!loadingMore()) {
                            <kbq-empty-state class="v-empty">
                                <div kbq-empty-state-title>Ничего не нашли</div>
                                <div kbq-empty-state-text>Ищем по действию, логину и объекту с учётом фильтров.</div>
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

                <kbq-content-panel>
                    @if (selected(); as a) {
                        <kbq-content-panel-header [vCpanelWidth]="panel">
                            <div kbqContentPanelHeaderTitle class="v-panel-title">{{ a.label }}</div>
                        </kbq-content-panel-header>
                    }
                    @if (selected(); as a) {
                        <kbq-content-panel-body>
                            <kbq-dl class="v-dl" [vertical]="false">
                                <kbq-dt>Когда</kbq-dt>
                                <kbq-dd>{{ when(a.created_at) }}</kbq-dd>
                                <kbq-dt>Кто</kbq-dt>
                                <kbq-dd
                                    ><span class="v-mono">{{ a.actor_login || dash }}</span></kbq-dd
                                >
                                @if (a.effective_login && a.effective_login !== a.actor_login) {
                                    <kbq-dt>За кого</kbq-dt>
                                    <kbq-dd
                                        ><span class="v-mono">{{ a.effective_login }}</span></kbq-dd
                                    >
                                }
                                <kbq-dt>Объект</kbq-dt>
                                <kbq-dd class="v-break-word">{{ a.object }}</kbq-dd>
                                <kbq-dt>Код действия</kbq-dt>
                                <kbq-dd
                                    ><span class="v-mono">{{ a.action }}</span></kbq-dd
                                >
                            </kbq-dl>
                            <section class="v-csec">
                                <div class="v-csec__title">Подробности</div>
                                @if (metaFiles(); as files) {
                                    <kbq-code-block
                                        [files]="files"
                                        [hideTabs]="true"
                                        [canDownload]="false"
                                        [softWrap]="true"
                                    />
                                } @else {
                                    <div class="v-secondary">Подробностей нет</div>
                                }
                            </section>
                        </kbq-content-panel-body>
                    }
                </kbq-content-panel>
            </kbq-content-panel-container>
        </div>
    `,
    styles: [
        ADMIN_LIST_STYLES,
        `
            .v-mt8 {
                margin-top: 8px;
            }
            .v-search {
                width: 240px;
                margin-left: auto;
            }
            .v-more {
                display: flex;
                justify-content: center;
                padding: 12px 0;
            }
            .v-break-word {
                overflow-wrap: anywhere;
            }
        `,
    ],
})
export class AuditComponent {
    readonly period = input<string>();
    readonly from = input<string>();
    readonly to = input<string>();
    readonly user = input<string>();
    readonly action = input<string>();
    readonly q = input<string>('');
    readonly event = input<string>();

    private readonly api = inject(ApiService);
    private readonly router = inject(Router);

    protected readonly colors = KbqComponentColors;
    protected readonly dash = DASH;
    protected readonly panel = cpanelWidth('audit');

    protected readonly loading = signal(true);
    protected readonly loadingMore = signal(false);
    protected readonly error = signal<unknown>(null);
    private readonly items = signal<AuditEvent[]>([]);
    private readonly nextCursor = signal<string | null>(null);
    private readonly ctx = signal<AuditObjectContext>({
        users: new Map(),
        tasks: new Map(),
        queues: new Map(),
        requests: new Map(),
    });
    /** Logins for the «Пользователь» pipe. */
    private readonly logins = signal<string[]>([]);

    protected readonly state = computed<AuditFilters>(() =>
        auditFiltersFromQuery({
            period: this.period(),
            from: this.from(),
            to: this.to(),
            user: this.user(),
            action: this.action(),
        }),
    );
    protected readonly pipesFiltered = computed(() => auditFiltered(this.state()));
    protected readonly filtered = computed(() => this.pipesFiltered() || !!this.q()?.trim());

    private readonly allRows = computed<AuditRow[]>(() => {
        const ctx = this.ctx();
        return this.items().map((e) => ({
            ...e,
            key: String(e.id),
            label: auditLabel(e.action),
            object: auditObjectLabel(e, ctx),
        }));
    });
    protected readonly rows = computed(() => {
        const tokens = tokenize(this.q() ?? '');
        return this.allRows().filter((a) =>
            matches([a.label, a.action, a.actor_login, a.effective_login, a.object], tokens),
        );
    });
    protected readonly selected = computed(() => {
        const id = this.event();
        return id ? (this.allRows().find((a) => a.key === id) ?? null) : null;
    });
    protected readonly metaFiles = computed<KbqCodeBlockFile[] | null>(() => {
        const json = auditMetadata(this.selected()?.metadata_json ?? null);
        return json ? [{ content: json, language: 'json', filename: 'metadata.json' }] : null;
    });

    private readonly userOptions = computed<KbqSelectValue[]>(() => {
        const set = new Set(this.logins());
        const current = this.state().user;
        if (current) set.add(current);
        return [
            { name: 'Все пользователи', value: null, id: '' },
            ...[...set].sort().map((l) => ({ name: l, value: l, id: l })),
        ];
    });
    private readonly actionOptions: KbqSelectValue[] = [
        { name: 'Все действия', value: null, id: '' },
        ...AUDIT_CATEGORIES.map((c) => ({ name: c.name, value: c.id, id: c.id })),
    ];

    protected readonly templates = computed<KbqPipeTemplate[]>(() => [
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
            name: 'Пользователь',
            id: 'user',
            type: KbqPipeTypes.Select,
            values: this.userOptions(),
            search: true,
            cleanable: true,
            removable: false,
            disabled: false,
            compareWith: byValue,
        },
        {
            name: 'Действие',
            id: 'action',
            type: KbqPipeTypes.Select,
            values: this.actionOptions,
            cleanable: true,
            removable: false,
            disabled: false,
            compareWith: byValue,
        },
    ]);

    protected readonly filter = computed<KbqFilter>(() => {
        const s = this.state();
        const user = s.user ? (this.userOptions().find((o) => o.value === s.user) ?? null) : null;
        const action = s.action ? (this.actionOptions.find((o) => o.value === s.action) ?? null) : null;
        const pipes: KbqPipe[] = [
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
                name: 'Пользователь',
                id: 'user',
                type: KbqPipeTypes.Select,
                value: user,
                search: true,
                cleanable: true,
                removable: false,
                disabled: false,
            },
            {
                name: 'Действие',
                id: 'action',
                type: KbqPipeTypes.Select,
                value: action,
                cleanable: true,
                removable: false,
                disabled: false,
            },
        ];
        return { name: '', pipes, readonly: false, disabled: false, changed: false, saved: false };
    });

    private loadSeq = 0;

    constructor() {
        void this.loadContext();
        effect(() => {
            const s = this.state();
            untracked(() => void this.load(s));
        });
        // Search and a deep-linked event look past the first page: fetch more until found or the list ends.
        effect(() => {
            const searching = !!this.q()?.trim();
            const id = this.event();
            const missing = !!id && !this.selected();
            const cursor = this.nextCursor();
            if (!cursor || this.loading() || this.loadingMore()) return;
            if ((searching || missing) && this.items().length < AUTO_LOAD_LIMIT) untracked(() => void this.loadMore());
        });
    }

    /** Names for the «Объект» column and logins for the user pipe. Failures only degrade to raw ids. */
    private async loadContext(): Promise<void> {
        const [users, tasks, queues, requests] = await Promise.allSettled([
            this.api.get<AdminUser[]>('/api/admin/users'),
            this.api.get<AdminTask[]>('/api/admin/tasks'),
            this.api.get<{ id: string; name: string }[]>('/api/admin/queues'),
            this.api.get<AdminRequest[]>('/api/admin/requests'),
        ]);
        const u = users.status === 'fulfilled' ? users.value : [];
        const requestTitles = new Map<string, string>();
        if (requests.status === 'fulfilled') {
            for (const r of requests.value) {
                requestTitles.set(String(r.id), r.title);
                requestTitles.set(String(r.chat_id), r.title);
            }
        }
        this.ctx.set({
            users: new Map(u.map((x) => [String(x.id), x.login])),
            tasks: new Map((tasks.status === 'fulfilled' ? tasks.value : []).map((t) => [String(t.id), t.title])),
            queues: new Map((queues.status === 'fulfilled' ? queues.value : []).map((x) => [String(x.id), x.name])),
            requests: requestTitles,
        });
        this.logins.set(u.map((x) => x.login));
    }

    private params(s: AuditFilters): Record<string, string> {
        return { ...auditApiParams(s, periodRange(s.period)), limit: String(PAGE_SIZE) };
    }

    protected reload(): void {
        void this.load(this.state());
    }

    private async load(s: AuditFilters): Promise<void> {
        const seq = ++this.loadSeq;
        this.loading.set(true);
        this.loadingMore.set(false);
        this.error.set(null);
        try {
            const page = await this.api.get<{ items: AuditEvent[]; next_cursor: string | null }>('/api/admin/audit', {
                params: this.params(s),
            });
            if (seq !== this.loadSeq) return;
            this.items.set(page.items);
            this.nextCursor.set(page.next_cursor);
        } catch (err) {
            if (seq === this.loadSeq) this.error.set(err);
        } finally {
            if (seq === this.loadSeq) this.loading.set(false);
        }
    }

    private async loadMore(): Promise<void> {
        const cursor = this.nextCursor();
        if (!cursor || this.loading() || this.loadingMore()) return;
        const seq = this.loadSeq;
        this.loadingMore.set(true);
        try {
            const page = await this.api.get<{ items: AuditEvent[]; next_cursor: string | null }>('/api/admin/audit', {
                params: { ...this.params(this.state()), cursor },
            });
            if (seq !== this.loadSeq) return;
            this.items.update((list) => [...list, ...page.items]);
            this.nextCursor.set(page.next_cursor);
        } catch {
            // Keep what is loaded; scrolling again retries.
        } finally {
            if (seq === this.loadSeq) this.loadingMore.set(false);
        }
    }

    protected onScroll(e: Event): void {
        const el = e.target as HTMLElement;
        if (el.scrollHeight - el.scrollTop - el.clientHeight < 200) void this.loadMore();
    }

    protected onPipe(pipe: KbqPipe): void {
        const s = this.state();
        const value = (pipe.value as KbqSelectValue | null)?.value ?? null;
        if (pipe.id === 'period') this.navigate({ ...s, period: periodFromPipe(pipe.value) });
        else if (pipe.id === 'user') this.navigate({ ...s, user: value ? String(value) : null });
        else if (pipe.id === 'action') this.navigate({ ...s, action: (value as AuditCategory | null) ?? null });
    }

    private navigate(s: AuditFilters, extra: Record<string, string | null> = {}): void {
        void this.router.navigate([], {
            queryParams: { ...auditFiltersToQuery(s), ...extra },
            queryParamsHandling: 'merge',
        });
    }

    protected resetFilters(): void {
        this.navigate({ period: null, user: null, action: null });
    }

    protected resetAll(): void {
        this.navigate({ period: null, user: null, action: null }, { q: null });
    }

    protected setQuery(q: string): void {
        void this.router.navigate([], {
            queryParams: { q: q || null },
            queryParamsHandling: 'merge',
            replaceUrl: true,
        });
    }

    protected openEvent(a: AuditRow): void {
        void this.router.navigate([], { queryParams: { event: a.key }, queryParamsHandling: 'merge' });
    }

    protected onPanelOpened(opened: boolean): void {
        if (!opened && this.event())
            void this.router.navigate([], { queryParams: { event: null }, queryParamsHandling: 'merge' });
    }

    protected when(iso: string): string {
        return formatWhen(iso, { timeForOld: true });
    }
}

function byValue(a: KbqSelectValue | null | undefined, b: KbqSelectValue | null | undefined): boolean {
    return !!a && !!b && a.value === b.value;
}
