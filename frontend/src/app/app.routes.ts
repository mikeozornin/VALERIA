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

const home = () => import('./features/home/home.component').then((m) => m.HomeComponent);
const search = () => import('./features/search/search.component').then((m) => m.SearchComponent);
const taskRun = () => import('./features/task-run/task-run.component').then((m) => m.TaskRunComponent);
const question = () => import('./features/question/question.component').then((m) => m.QuestionComponent);
const folder = () => import('./features/folder/folder.component').then((m) => m.FolderComponent);

function screen(path: string, name: string): Route {
    return { path, loadComponent: placeholder, data: { screen: name } };
}

function denied(path: string, section: string, reason: DeniedReason): Route {
    return { path, loadComponent: accessDenied, data: { section, reason } };
}

/** Admin screen + its access-denied twin. `section` is the «Система» item the path belongs to. */
function admin(path: string, section: string, name = section): Route[] {
    return [{ ...screen(path, name), canMatch: [adminMatch] }, denied(path, section, 'admin')];
}

/** Replaces the placeholder of the real (first) route of an `admin()` pair with the screen's component. */
function loaded(load: NonNullable<Route['loadComponent']>, routes: Route[]): Route[] {
    return routes.map((r, i) => (i === 0 ? { ...r, loadComponent: load } : r));
}

/** Adds the app-wide unsaved-changes guard to every leaf route. */
function withDirtyGuard(routes: Route[]): Route[] {
    return routes.map((r) => (r.loadComponent || r.component ? { ...r, canDeactivate: [dirtyGuard] } : r));
}

export const FOLDER_SECTION = 'Работа в папке';

const shellRoutes: Route[] = withDirtyGuard([
    { ...screen('', 'Новый запрос'), loadComponent: home },
    { ...screen('new/:taskId', 'Новый запрос'), loadComponent: home },
    { ...screen('search', 'Поиск и команды'), loadComponent: search },
    { ...screen('questions/:id', 'Вопрос'), loadComponent: question },
    { ...screen('tasks/runs/:id', 'Запуск задачи'), loadComponent: taskRun },
    { ...screen('folder/:id', FOLDER_SECTION), loadComponent: folder, canMatch: [notTasksOnlyMatch] },
    denied('folder/:id', FOLDER_SECTION, 'tasks-only'),

    ...admin('admin/statistics', 'Статистика'),
    ...loaded(
        () => import('./features/admin/users/users.component').then((m) => m.UsersComponent),
        admin('admin/users', 'Пользователи'),
    ),
    ...loaded(
        () => import('./features/admin/users/user-create.component').then((m) => m.UserCreateComponent),
        admin('admin/users/new', 'Пользователи', 'Новый пользователь'),
    ),
    ...loaded(
        () => import('./features/admin/users/user-edit.component').then((m) => m.UserEditComponent),
        admin('admin/users/:login/edit', 'Пользователи', 'Изменение пользователя'),
    ),
    ...admin('admin/tasks', 'Настройка задач'),
    ...admin('admin/tasks/new', 'Настройка задач', 'Новая задача'),
    ...admin('admin/tasks/:id', 'Настройка задач', 'Задача'),
    ...admin('admin/queue', 'Очередь'),
    ...admin('admin/queue/settings', 'Очередь', 'Настройки очереди'),
    ...admin('admin/queue/settings/edit', 'Очередь', 'Изменение настроек очереди'),
    ...loaded(
        () => import('./features/admin/requests/requests.component').then((m) => m.RequestsComponent),
        admin('admin/requests', 'Запросы пользователей'),
    ),
    ...loaded(
        () => import('./features/admin/requests/request-view.component').then((m) => m.RequestViewComponent),
        admin('admin/requests/:id', 'Запросы пользователей', 'Запрос'),
    ),
    ...loaded(
        () => import('./features/admin/audit/audit.component').then((m) => m.AuditComponent),
        admin('admin/audit', 'Аудит'),
    ),
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
