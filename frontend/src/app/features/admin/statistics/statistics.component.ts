import { ChangeDetectionStrategy, Component, computed, effect, inject, signal, TemplateRef, viewChild } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { ActivatedRoute, Router } from '@angular/router';
import { LuxonDateAdapter } from '@koobiq/angular-luxon-adapter/adapter';
import { DateAdapter, DateFormatter, KBQ_DATE_LOCALE } from '@koobiq/components/core';
import { KbqEmptyStateModule } from '@koobiq/components/empty-state';
import { KbqFilter, KbqFilterBarModule, KbqPipe, KbqPipeTemplate, KbqPipeTypes } from '@koobiq/components/filter-bar';
import { PageStateComponent } from '../../../shared/page-state/page-state.component';
import { ScreenHeaderComponent } from '../../../shell/screen-header/screen-header.component';
import { AdminConfigApi, AdminTask, StatRunsResponse } from '../config-shared/admin-config.api';
import { dur, EMPTY, plural } from '../config-shared/format';
import { sortByName } from '../config-shared/names';
import { ControlChartComponent } from './control-chart.component';
import { ShapeComponent, ShapeName, TASK_SHAPES } from './shape.component';
import { kpis, parsePeriod, Period, periodParam, periodRange, PERIODS, toChartRun } from './stats-math';

const PERIOD_PIPE = 'Период';
const TYPE_PIPE = 'Тип';

interface TypeOption {
    name: string;
    value: string;
    /** Mark shape shown before the name in the dropdown; none for «Все типы» / «Все задачи». */
    shape: ShapeName | null;
}

/**
 * «Статистика» (SPEC §10.1, prototype step «stats»): filter bar (period is required, type is optional),
 * four KPIs, the control chart with a histogram, and the data source line. Filters live in `?period=&type=`.
 */
@Component({
    selector: 'v-statistics',
    // The Luxon adapter only knows full locale names: with the app LOCALE_ID 'ru' it throws on creation. Provided on the
    // element so the filter-bar date pipe (and its formatter) resolve these before the root ones.
    providers: [
        { provide: KBQ_DATE_LOCALE, useValue: 'ru-RU' },
        { provide: DateAdapter, useClass: LuxonDateAdapter },
        DateFormatter,
    ],
    imports: [
        ScreenHeaderComponent,
        PageStateComponent,
        KbqFilterBarModule,
                KbqEmptyStateModule,
        ControlChartComponent,
        ShapeComponent,
    ],
    changeDetection: ChangeDetectionStrategy.OnPush,
    template: `
        <v-screen-header title="Статистика" />
        <v-page-state [loading]="loading()" [error]="error()" (retry)="load()">
            <div class="v-content">
                <kbq-filter-bar [filter]="filter()" [pipeTemplates]="templates()" (onChangePipe)="onPipe($event)">
                    @for (pipe of filter().pipes; track pipe.name) {
                        <ng-container *kbqPipe="pipe" />
                    }
                    @if (type()) {
                        <kbq-filter-reset (onResetFilter)="setType(null)" />
                    }
                </kbq-filter-bar>

                @let k = stats();
                <div class="v-kpis">
                    <div class="v-kpi">
                        <div class="v-kpi__v">{{ k.count }}</div>
                        <div class="v-kpi__t">{{ countText(k.count) }} за период</div>
                    </div>
                    <div class="v-kpi">
                        <div class="v-kpi__v">{{ k.medianWork === null ? EMPTY : dur(k.medianWork) }}</div>
                        <div class="v-kpi__t">медиана работы агента</div>
                    </div>
                    <div class="v-kpi">
                        <div class="v-kpi__v">{{ k.medianWait === null ? EMPTY : dur(k.medianWait) }}</div>
                        <div class="v-kpi__t">медиана ожидания в очереди</div>
                    </div>
                    <div class="v-kpi">
                        <div class="v-kpi__v">{{ k.failed }}</div>
                        <div class="v-kpi__t">с ошибкой{{ k.failedPct === null ? '' : ' · ' + k.failedPct + '%' }}</div>
                    </div>
                </div>

                <section class="v-chart">
                    <h2 class="v-chart__title">Сколько длится каждый запуск</h2>
                    <p class="v-chart__sub">
                        Метка — один запуск: по горизонтали время, когда он встал в очередь, по вертикали сколько работал агент. Черта над
                        меткой — ожидание в очереди. Цвет — итог: <svg vShape="circle" st="done"></svg> успешно,
                        <svg vShape="circle" st="failed"></svg> с ошибкой, <svg vShape="circle" st="cancelled"></svg> отменён,
                        <svg vShape="circle" st="running"></svg> ещё идёт.
                    </p>
                    <div class="v-keys">
                        @for (key of keys(); track key.shapeKey) {
                            <span class="v-key"><svg [vShape]="key.shape" st="key"></svg>{{ key.label }}</span>
                        }
                    </div>
                    @if (runs().length) {
                        <v-control-chart [runs]="runs()" [from]="range().from" [to]="range().to" [now]="now()" [shapes]="shapes()" />
                    } @else if (data()) {
                        <kbq-empty-state class="v-chart__empty">
                            <div kbq-empty-state-title>За этот период запусков нет</div>
                            <div kbq-empty-state-text>Выберите другой период или тип.</div>
                        </kbq-empty-state>
                    }
                </section>

                <p class="v-source">
                    Источник: вопросы и запуски задач VALERIA, часовой пояс Europe/Moscow, данные с 27 июля 2026. Медиана и 90% считаются по
                    успешно завершённым запускам. Работа в папке не входит: это диалог, его длительность зависит от человека.
                </p>
            </div>
        </v-page-state>
        <ng-template #typeOption let-option="option">
            <span class="v-type-opt">
                @if (option.shape) {
                    <svg [vShape]="option.shape" st="key"></svg>
                } @else {
                    <span class="v-type-opt__gap"></span>
                }
                {{ option.name }}
            </span>
        </ng-template>
    `,
    styles: `
        :host { display: flex; flex-direction: column; flex: 1 1 auto; min-height: 0; }
        .v-content { flex: 1 1 auto; min-height: 0; overflow: auto; padding: var(--kbq-size-l) var(--kbq-size-xxl) var(--kbq-size-xxl); }
        .v-kpis { display: flex; flex-wrap: wrap; gap: 48px; margin-top: var(--kbq-size-l); }
        .v-kpi__v {
            font-size: 32px;
            line-height: 40px;
            font-weight: 700;
            letter-spacing: -0.02em;
            font-variant-numeric: tabular-nums;
            color: var(--kbq-foreground-contrast);
        }
        .v-kpi__t { color: var(--kbq-foreground-contrast-secondary); }
        .v-chart { margin-top: var(--kbq-size-3xl); }
        .v-chart__title {
            margin: 0;
            font-size: var(--kbq-typography-subheading-font-size);
            line-height: var(--kbq-typography-subheading-line-height);
            font-weight: 600;
        }
        .v-chart__sub { margin: var(--kbq-size-xxs) 0 0; max-width: 880px; color: var(--kbq-foreground-contrast-secondary); }
        .v-keys {
            display: flex;
            flex-wrap: wrap;
            gap: var(--kbq-size-xxs) 20px;
            margin: var(--kbq-size-s) 0 20px;
            font-size: var(--kbq-typography-text-compact-font-size);
            line-height: var(--kbq-typography-text-compact-line-height);
            color: var(--kbq-foreground-contrast-secondary);
        }
        .v-key { display: inline-flex; align-items: center; gap: 6px; }
        .v-type-opt { display: inline-flex; align-items: center; gap: var(--kbq-size-s); }
        .v-type-opt__gap { width: 12px; flex: none; }
        /* Koobiq's select pipe has no chevron; the prototype shows one while the optional «Тип» is empty. */
        :host ::ng-deep kbq-pipe-select.kbq-pipe_empty .kbq-pipe__value_empty::after {
            content: '\\f5cb'; /* kbq-chevron-down_16 */
            font-family: 'Koobiq Icons';
            font-size: 16px;
            line-height: 1;
            vertical-align: -3px;
            color: var(--kbq-icon-contrast-fade);
        }
        .v-chart__empty { padding: var(--kbq-size-3xl) 0; }
        .v-source {
            margin: var(--kbq-size-3xl) 0 0;
            padding-top: var(--kbq-size-m);
            border-top: 1px solid var(--kbq-line-contrast-less);
            color: var(--kbq-foreground-contrast-tertiary);
            font-size: var(--kbq-typography-text-compact-font-size);
            line-height: var(--kbq-typography-text-compact-line-height);
        }
    `,
})
export class StatisticsComponent {
    private readonly api = inject(AdminConfigApi);
    private readonly router = inject(Router);
    private readonly route = inject(ActivatedRoute);
    private readonly query = toSignal(this.route.queryParamMap, { initialValue: this.route.snapshot.queryParamMap });

    protected readonly EMPTY = EMPTY;
    protected readonly dur = dur;
    protected readonly countText = (n: number) => plural(n, 'запуск', 'запуска', 'запусков');

    protected readonly period = computed<Period>(() => parsePeriod(this.query().get('period')));
    protected readonly type = computed(() => this.query().get('type') || null);

    protected readonly tasks = signal<AdminTask[]>([]);
    protected readonly data = signal<StatRunsResponse | null>(null);
    protected readonly loading = signal(false);
    protected readonly error = signal<unknown>(null);
    /** Absolute range of the loaded data: fixed when the request is sent, so a preset does not drift. */
    protected readonly range = signal({ from: 0, to: 1 });

    protected readonly now = computed(() => {
        const at = this.data()?.generated_at;
        return at ? Math.min(Date.parse(at), Math.max(this.range().to, Date.now())) : Date.now();
    });
    protected readonly runs = computed(() => {
        const now = this.now();
        return (this.data()?.items ?? []).map((r) => toChartRun(r, now));
    });
    protected readonly stats = computed(() => kpis(this.runs()));

    /** Task shapes in a fixed order (by name), so a filter never repaints the survivors; «?» for questions. */
    protected readonly shapes = computed<Record<string, ShapeName>>(() => {
        const out: Record<string, ShapeName> = { q: 'question' };
        this.sortedTasks().forEach((t, i) => (out[t.id] = TASK_SHAPES[i % TASK_SHAPES.length]));
        return out;
    });
    private readonly sortedTasks = computed(() => sortByName(this.tasks(), (t) => t.title));
    private readonly typeOptionTpl = viewChild<TemplateRef<unknown>>('typeOption');
    protected readonly keys = computed(() => {
        const type = this.type();
        const shapes = this.shapes();
        const q = { shapeKey: 'q', shape: 'question' as ShapeName, label: 'вопрос' };
        const tasks = this.sortedTasks().map((t) => ({ shapeKey: t.id, shape: shapes[t.id], label: lowerFirst(t.title) }));
        if (!type) return [q, ...tasks];
        if (type === 'question') return [q];
        if (type === 'task') return tasks;
        return tasks.filter((t) => t.shapeKey === type);
    });

    private readonly typeOptions = computed<TypeOption[]>(() => [
        { name: 'Все типы', value: '', shape: null },
        { name: 'Вопросы', value: 'question', shape: 'question' },
        { name: 'Все задачи', value: 'task', shape: null },
        ...this.sortedTasks().map((t) => ({ name: t.title, value: t.id, shape: this.shapes()[t.id] })),
    ]);

    protected readonly templates = computed<KbqPipeTemplate[]>(() => [
        {
            name: PERIOD_PIPE,
            type: KbqPipeTypes.Datetime,
            values: PERIODS.map((p) => ({ name: p.name, start: p.start, end: null })),
            cleanable: false,
            removable: false,
            disabled: false,
        },
        {
            name: TYPE_PIPE,
            type: KbqPipeTypes.Select,
            values: this.typeOptions(),
            valueTemplate: this.typeOptionTpl(),
            compareWith: (a, b) => !!a && !!b && a.value === b.value,
            cleanable: false,
            removable: false,
            disabled: false,
        },
    ]);

    protected readonly filter = computed<KbqFilter>(() => {
        const p = this.period();
        const type = this.type();
        return {
            name: '',
            readonly: false,
            disabled: false,
            changed: false,
            saved: false,
            pipes: [
                {
                    name: PERIOD_PIPE,
                    type: KbqPipeTypes.Datetime,
                    value: 'preset' in p ? { name: p.preset.name, start: p.preset.start, end: null } : { start: p.from, end: p.to },
                    cleanable: false,
                    removable: false,
                    disabled: false,
                },
                {
                    name: TYPE_PIPE,
                    type: KbqPipeTypes.Select,
                    value: type ? (this.typeOptions().find((o) => o.value === type) ?? { name: type, value: type, shape: null }) : null,
                    cleanable: false,
                    removable: false,
                    disabled: false,
                },
            ],
        };
    });

    constructor() {
        this.api.tasks().then(
            (t) => this.tasks.set(t),
            () => undefined, // type options degrade to «Вопросы» / «Все задачи»; the runs request reports errors
        );
        effect(() => {
            this.period();
            this.type();
            void this.load();
        });
    }

    private seq = 0;

    async load(): Promise<void> {
        const p = this.period();
        const type = this.type();
        const range = periodRange(p, Date.now());
        const seq = ++this.seq;
        this.loading.set(true);
        this.error.set(null);
        try {
            const res = await this.api.statRuns({ from: new Date(range.from).toISOString(), to: new Date(range.to).toISOString(), type });
            if (seq !== this.seq) return;
            this.range.set(range);
            this.data.set(res);
        } catch (err) {
            if (seq === this.seq) this.error.set(err);
        } finally {
            if (seq === this.seq) this.loading.set(false);
        }
    }

    protected onPipe(pipe: KbqPipe): void {
        if (pipe.name === PERIOD_PIPE) {
            const v = pipe.value as { name?: string; start?: string; end?: string } | null;
            const preset = v?.name ? PERIODS.find((p) => p.name === v.name) : undefined;
            if (preset) this.navigate({ period: periodParam({ preset }) });
            else if (v?.start && v?.end) this.navigate({ period: periodParam({ from: v.start, to: v.end }) });
        } else if (pipe.name === TYPE_PIPE) {
            this.setType((pipe.value as TypeOption | null)?.value || null);
        }
    }

    protected setType(type: string | null): void {
        this.navigate({ type });
    }

    private navigate(params: { period?: string; type?: string | null }): void {
        void this.router.navigate([], { relativeTo: this.route, queryParams: params, queryParamsHandling: 'merge' });
    }
}

const lowerFirst = (s: string) => (s && !/^[A-ZА-ЯЁ]{2}/.test(s) ? s[0].toLocaleLowerCase('ru') + s.slice(1) : s);
