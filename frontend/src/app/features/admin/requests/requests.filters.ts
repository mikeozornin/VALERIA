import { ItemKind } from '../../../core/status/status';
import { PeriodState } from '../shared/period';

/** Filters of «Запросы пользователей» as they live in the URL (search `q` is client-side and not here). */
export interface RequestFilters {
    users: string[];
    period: PeriodState;
    types: ItemKind[];
    /** Task id; only meaningful while «Задача» is among the types. */
    task: string | null;
}

/** Query params for `GET /api/admin/requests`; empty filters are left out. */
export function requestFilterParams(
    filters: RequestFilters,
    range: { from: string | null; to: string | null },
): Record<string, string> {
    const params: Record<string, string> = {};
    if (filters.users.length) params['user'] = filters.users.join(',');
    if (filters.types.length) params['type'] = filters.types.join(',');
    if (filters.task && (!filters.types.length || filters.types.includes('task'))) params['task'] = filters.task;
    if (range.from) params['from'] = range.from;
    if (range.to) params['to'] = range.to;
    return params;
}
