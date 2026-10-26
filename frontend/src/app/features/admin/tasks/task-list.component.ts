import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';
import { Router, RouterLink } from '@angular/router';
import { KbqBadgeColors, KbqBadgeModule } from '@koobiq/components/badge';
import { KbqButtonModule } from '@koobiq/components/button';
import { KbqComponentColors } from '@koobiq/components/core';
import { KbqIconModule } from '@koobiq/components/icon';
import { KbqLinkModule } from '@koobiq/components/link';
import { KbqTableModule } from '@koobiq/components/table';
import { PageStateComponent } from '../../../shared/page-state/page-state.component';
import { ScreenHeaderComponent } from '../../../shell/screen-header/screen-header.component';
import { AdminConfigApi, AdminTask, isOn } from '../config-shared/admin-config.api';
import { changedLabel, EMPTY, plural } from '../config-shared/format';
import { sortByName } from '../config-shared/names';
import { audienceLabel } from './task-form';

const users = (n: number) => plural(n, 'пользователь', 'пользователя', 'пользователей');

/** «Настройка задач» list (prototype `ataskView` list): a row opens the full-page form. */
@Component({
    selector: 'v-task-list',
    imports: [ScreenHeaderComponent, PageStateComponent, KbqTableModule, KbqBadgeModule, KbqButtonModule, KbqIconModule, KbqLinkModule, RouterLink],
    changeDetection: ChangeDetectionStrategy.OnPush,
    template: `
        <v-screen-header title="Настройка задач">
            <ng-container vHeaderAfterTitle>
                @if (rows()) {
                    <kbq-badge [badgeColor]="badgeColors.FadeContrast">{{ rows()!.length }}</kbq-badge>
                }
            </ng-container>
            <ng-container vHeaderActions>
                <a kbq-button [color]="colors.Theme" routerLink="/admin/tasks/new">
                    <i kbq-icon="kbq-plus_16"></i>
                    Создать задачу
                </a>
            </ng-container>
        </v-screen-header>
        <v-page-state [loading]="loading()" [error]="error()" (retry)="load()">
            <div class="v-content">
                <table kbq-table [border]="true" class="v-table">
                    <thead>
                        <tr>
                            <th>Название</th>
                            <th>Статус</th>
                            <th>Кто видит</th>
                            <th>Модель</th>
                            <th>Затем запускается</th>
                            <th>Изменена</th>
                        </tr>
                    </thead>
                    <tbody>
                        @for (r of rows(); track r.id) {
                            <tr class="v-row" (click)="open(r.id, $event)">
                                <td><a kbq-link class="v-title" [routerLink]="['/admin/tasks', r.id]">{{ r.title }}</a></td>
                                <td>
                                    <kbq-badge [badgeColor]="r.published ? badgeColors.FadeSuccess : badgeColors.FadeContrast">
                                        {{ r.published ? 'Опубликована' : 'Черновик' }}
                                    </kbq-badge>
                                </td>
                                <td class="v-nowrap">{{ r.audience }}</td>
                                <td class="v-mono v-nowrap">{{ r.model }}</td>
                                <td>{{ r.next }}</td>
                                <td class="v-secondary v-nowrap">{{ r.changed }}</td>
                            </tr>
                        } @empty {
                            <tr>
                                <td colspan="6" class="v-secondary v-empty">Задач пока нет</td>
                            </tr>
                        }
                    </tbody>
                </table>
            </div>
        </v-page-state>
    `,
    styles: `
        :host { display: flex; flex-direction: column; flex: 1 1 auto; min-height: 0; }
        .v-content { flex: 1 1 auto; min-height: 0; overflow: auto; padding: var(--kbq-size-l) var(--kbq-size-xxl) var(--kbq-size-xxl); }
        .v-table { width: 100%; }
        .v-table td { vertical-align: middle; }
        .v-table th { white-space: nowrap; }
        .v-row { cursor: pointer; }
        .v-title.kbq-link { color: var(--kbq-foreground-contrast); text-decoration: none; }
        .v-row:hover .v-title.kbq-link { color: var(--kbq-foreground-theme); }
        .v-mono { font-family: var(--kbq-typography-mono-normal-font-family); }
        .v-nowrap { white-space: nowrap; }
        .v-secondary { color: var(--kbq-foreground-contrast-secondary); }
        .v-empty { text-align: center; padding: var(--kbq-size-xxl) 0; }
    `,
})
export class TaskListComponent {
    private readonly api = inject(AdminConfigApi);
    private readonly router = inject(Router);

    protected readonly colors = KbqComponentColors;
    protected readonly badgeColors = KbqBadgeColors;
    protected readonly loading = signal(true);
    protected readonly error = signal<unknown>(null);
    private readonly tasks = signal<AdminTask[] | null>(null);

    protected readonly rows = computed(() => {
        const tasks = this.tasks();
        if (!tasks) return null;
        const title = new Map(tasks.map((t) => [t.id, t.title]));
        return sortByName(tasks, (t) => t.title).map((t) => ({
            id: t.id,
            title: t.title,
            published: isOn(t.is_published),
            audience: audienceLabel(t, users),
            model: t.model_id || EMPTY,
            next: (t.next_task_ids ?? []).map((id) => title.get(id)).filter(Boolean).join(', ') || EMPTY,
            changed: changedLabel(t.updated_at),
        }));
    });

    constructor() {
        void this.load();
    }

    async load(): Promise<void> {
        this.loading.set(true);
        this.error.set(null);
        try {
            this.tasks.set(await this.api.tasks());
        } catch (err) {
            this.error.set(err);
        } finally {
            this.loading.set(false);
        }
    }

    /** The whole row is clickable; the title link stays the keyboard and middle-click target. */
    protected open(id: string, event: MouseEvent): void {
        if ((event.target as HTMLElement).closest('a')) return;
        void this.router.navigate(['/admin/tasks', id]);
    }
}
