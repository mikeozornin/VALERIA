import { Route, Routes } from '@angular/router';
import { authMatch, guestMatch } from './core/guards/auth.guard';
import { dirtyGuard } from './core/guards/dirty.guard';
import { adminMatch, notTasksOnlyMatch } from './core/guards/role.guard';
import type { DeniedReason } from './shared/access-denied/access-denied.component';

/**
 * Routes (SPEC §5). Every screen is a lazy placeholder until its worker replaces `loadComponent`.
 *
 * Role-restricted paths come in pairs: the real route behind `canMatch`, then the same path rendering
 * «Раздел недоступен» — the URL stays as typed and nothing is requested from the API.
 */
const placeholder = () => import('./shared/placeholder/placeholder.component').then((m) => m.PlaceholderComponent);
const accessDenied = () =>
    import('./shared/access-denied/access-denied.component').then((m) => m.AccessDeniedComponent);

function screen(path: string, name: string): Route {
    return { path, loadComponent: placeholder, data: { screen: name } };
}

function denied(path: string, section: string, reason: DeniedReason): Route {
    return { path, loadComponent: accessDenied, data: { section, reason } };
}

/** Admin screen + its access-denied twin. `section` is the «Система» item the path belongs to. */
function admin(path: string, section: string, name = section, load?: Route['loadComponent']): Route[] {
    const real = { ...screen(path, name), canMatch: [adminMatch] };
    return [load ? { ...real, loadComponent: load } : real, denied(path, section, 'admin')];
}

/** Adds the app-wide unsaved-changes guard to every leaf route. */
function withDirtyGuard(routes: Route[]): Route[] {
    return routes.map((r) => (r.loadComponent || r.component ? { ...r, canDeactivate: [dirtyGuard] } : r));
}

export const FOLDER_SECTION = 'Работа в папке';

const shellRoutes: Route[] = withDirtyGuard([
    screen('', 'Новый запрос'),
    screen('new/:taskId', 'Новый запрос'),
    screen('search', 'Поиск и команды'),
    screen('questions/:id', 'Вопрос'),
    screen('tasks/runs/:id', 'Запуск задачи'),
    { ...screen('folder/:id', FOLDER_SECTION), canMatch: [notTasksOnlyMatch] },
    denied('folder/:id', FOLDER_SECTION, 'tasks-only'),

    ...admin('admin/statistics', 'Статистика', 'Статистика', () =>
        import('./features/admin/statistics/statistics.component').then((m) => m.StatisticsComponent),
    ),
    ...admin('admin/users', 'Пользователи'),
    ...admin('admin/users/new', 'Пользователи', 'Новый пользователь'),
    ...admin('admin/users/:login/edit', 'Пользователи', 'Изменение пользователя'),
    ...admin('admin/tasks', 'Настройка задач'),
    ...admin('admin/tasks/new', 'Настройка задач', 'Новая задача'),
    ...admin('admin/tasks/:id', 'Настройка задач', 'Задача'),
    ...admin('admin/queue', 'Очередь'),
    ...admin('admin/queue/settings', 'Очередь', 'Настройки очереди'),
    ...admin('admin/queue/settings/edit', 'Очередь', 'Изменение настроек очереди'),
    ...admin('admin/requests', 'Запросы пользователей'),
    ...admin('admin/requests/:id', 'Запросы пользователей', 'Запрос'),
    ...admin('admin/audit', 'Аудит'),
    // Any other path inside the app: home (no extra «not found» screen in SPEC §5 besides «Чат не найден»).
    { path: '**', redirectTo: '' },
]);

export const routes: Routes = [
    {
        path: 'login',
        canMatch: [guestMatch],
        loadComponent: () => import('./features/login/login.component').then((m) => m.LoginComponent),
    },
    {
        path: '',
        canMatch: [authMatch],
        loadComponent: () => import('./shell/shell.component').then((m) => m.ShellComponent),
        children: shellRoutes,
    },
];
