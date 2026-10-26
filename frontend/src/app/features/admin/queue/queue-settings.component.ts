import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import { KbqBadgeColors, KbqBadgeModule } from '@koobiq/components/badge';
import { KbqButtonModule } from '@koobiq/components/button';
import { KbqComponentColors } from '@koobiq/components/core';
import { KbqDlModule } from '@koobiq/components/dl';
import { KbqIconModule } from '@koobiq/components/icon';
import { KbqLinkModule } from '@koobiq/components/link';
import { KbqTableModule } from '@koobiq/components/table';
import { PageStateComponent } from '../../../shared/page-state/page-state.component';
import { ScreenHeaderComponent } from '../../../shell/screen-header/screen-header.component';
import { AdminConfigApi, AdminTask, ConsultationSettings, isOn, QueueOverview } from '../config-shared/admin-config.api';
import { EMPTY } from '../config-shared/format';
import { isOverridden, sessions, usedBy } from './queue-form';
import { QueueTabsComponent } from './queue-tabs.component';

/** «Очередь» → «Очереди и лимиты», read-only (SPEC §10.3): changes happen on a separate form page via «Изменить». */
@Component({
    selector: 'v-queue-settings',
    imports: [ScreenHeaderComponent, PageStateComponent, QueueTabsComponent, KbqTableModule, KbqBadgeModule, KbqButtonModule, KbqDlModule, KbqIconModule, KbqLinkModule, RouterLink],
    changeDetection: ChangeDetectionStrategy.OnPush,
    template: `
        <v-screen-header title="Очередь">
            <ng-container vHeaderActions>
                <a kbq-button [color]="colors.ContrastFade" routerLink="/admin/queue/settings/edit">
                    <i kbq-icon="kbq-pencil_16"></i>
                    Изменить
                </a>
            </ng-container>
        </v-screen-header>
        <v-page-state [loading]="loading()" [error]="error()" (retry)="load()">
            <div class="v-content">
                <v-queue-tabs active="settings" />
                @if (data(); as d) {
                    <div class="v-editor">
                        <section class="v-rosec" aria-labelledby="q-limit">
                            <h2 class="v-rosec__title" id="q-limit">Общий лимит</h2>
                            <div>
                                <b>{{ d.ov.limit }} {{ sessionsWord(d.ov.limit) }}</b>
                                работают одновременно
                            </div>
                            <div class="v-hint">
                                @if (overridden()) {
                                    Задано здесь. В конфигурации сервера {{ d.ov.env_limit }}, максимум {{ d.ov.hard_limit }}.
                                } @else {
                                    Берётся из конфигурации сервера. Максимум {{ d.ov.hard_limit }}.
                                }
                                Сессии, которые ждут ответа пользователя, слот не занимают.
                            </div>
                        </section>
                        <section class="v-rosec" aria-labelledby="q-queues">
                            <h2 class="v-rosec__title" id="q-queues">Очереди</h2>
                            <div class="v-hint">Свободный слот получает очередь с наибольшим приоритетом.</div>
                            <table kbq-table [border]="true" class="v-table">
                                <thead>
                                    <tr>
                                        <th>Название</th>
                                        <th class="v-num">Приоритет</th>
                                        <th class="v-num">Воркеров, не больше</th>
                                        <th>Сейчас</th>
                                        <th>Используется</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    @for (q of queues(); track q.id) {
                                        <tr>
                                            <td>
                                                {{ q.name }}
                                                @if (q.system) {
                                                    <kbq-badge [compact]="true" [badgeColor]="badgeColors.FadeContrast">системная</kbq-badge>
                                                }
                                            </td>
                                            <td class="v-num">{{ q.priority }}</td>
                                            <td class="v-num">{{ q.max }}</td>
                                            <td class="v-secondary v-nowrap">{{ q.active }} в работе · {{ q.queued }} ждут</td>
                                            <td class="v-secondary">{{ q.used }}</td>
                                        </tr>
                                    }
                                </tbody>
                            </table>
                        </section>
                        <section class="v-rosec" aria-labelledby="q-route">
                            <h2 class="v-rosec__title" id="q-route">Куда попадают запросы</h2>
                            <kbq-dl [vertical]="false">
                                <kbq-dt>Вопросы</kbq-dt>
                                <kbq-dd>
                                    Очередь «{{ queueName(d.consult.queue_id) }}» ·
                                    <span class="v-mono">{{ d.consult.model }}</span>
                                    · Build, только чтение
                                </kbq-dd>
                                <kbq-dt>Работа в папке</kbq-dt>
                                <kbq-dd>Очередь «{{ queueName(d.ov.default_chat_queue_id) }}» · модель пользователь выбирает сам</kbq-dd>
                                <kbq-dt>Задачи</kbq-dt>
                                <kbq-dd>Очередь и модель задаются в каждой задаче · <a kbq-link routerLink="/admin/tasks">Настройка задач</a></kbq-dd>
                            </kbq-dl>
                        </section>
                    </div>
                }
            </div>
        </v-page-state>
    `,
    styles: `
        :host { display: flex; flex-direction: column; flex: 1 1 auto; min-height: 0; }
        .v-content { flex: 1 1 auto; min-height: 0; overflow: auto; padding: var(--kbq-size-l) var(--kbq-size-xxl) var(--kbq-size-xxl); }
        .v-editor { max-width: 960px; display: flex; flex-direction: column; gap: var(--kbq-size-3xl); }
        .v-rosec { display: flex; flex-direction: column; gap: var(--kbq-size-s); }
        .v-rosec__title {
            margin: 0;
            font-family: var(--kbq-typography-text-big-strong-font-family);
            font-size: var(--kbq-typography-text-big-strong-font-size);
            line-height: var(--kbq-typography-text-big-strong-line-height);
            font-weight: var(--kbq-typography-text-big-strong-font-weight);
        }
        .v-hint {
            color: var(--kbq-foreground-contrast-secondary);
            font-size: var(--kbq-typography-text-compact-font-size);
            line-height: var(--kbq-typography-text-compact-line-height);
        }
        .v-table { width: 100%; }
        .v-table td { vertical-align: middle; }
        .v-num { text-align: right; }
        .v-secondary { color: var(--kbq-foreground-contrast-secondary); }
        .v-nowrap { white-space: nowrap; }
        .v-mono { font-family: var(--kbq-typography-mono-normal-font-family); }
    `,
})
export class QueueSettingsComponent {
    private readonly api = inject(AdminConfigApi);

    protected readonly colors = KbqComponentColors;
    protected readonly badgeColors = KbqBadgeColors;
    protected readonly sessionsWord = sessions;
    protected readonly loading = signal(true);
    protected readonly error = signal<unknown>(null);
    protected readonly data = signal<{ ov: QueueOverview; consult: ConsultationSettings; tasks: AdminTask[] } | null>(null);

    protected readonly overridden = computed(() => (this.data() ? isOverridden(this.data()!.ov) : false));
    protected readonly queues = computed(() => {
        const d = this.data();
        if (!d) return [];
        const ctx = { consultQueue: d.consult.queue_id, chatQueue: d.ov.default_chat_queue_id, taskQueues: d.tasks.map((t) => t.queue_id) };
        return d.ov.queues
            .filter((q) => q.status === 'active')
            .map((q) => ({
                id: q.id,
                name: q.name,
                system: isOn(q.is_system),
                priority: q.priority,
                max: q.max_workers,
                active: q.active,
                queued: q.queued,
                used: usedBy(q.id, ctx) || EMPTY,
            }));
    });

    constructor() {
        void this.load();
    }

    async load(): Promise<void> {
        this.loading.set(true);
        this.error.set(null);
        try {
            const [ov, consult, tasks] = await Promise.all([this.api.queue(), this.api.consultations(), this.api.tasks()]);
            this.data.set({ ov, consult, tasks });
        } catch (err) {
            this.error.set(err);
        } finally {
            this.loading.set(false);
        }
    }

    protected queueName(id: string): string {
        return this.data()?.ov.queues.find((q) => q.id === id)?.name ?? EMPTY;
    }
}
