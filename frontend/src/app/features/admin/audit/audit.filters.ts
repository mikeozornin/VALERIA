import { auditCategoryCodes, AuditCategory, isAuditCategory } from '../shared/audit-labels';
import { periodFromQuery, PeriodState, periodToQuery } from '../shared/period';

/** «Аудит» filters as they live in the URL: `?period=&from=&to=&user=&action=`. */
export interface AuditFilters {
    period: PeriodState;
    /** Login: matches who acted or on whose behalf. */
    user: string | null;
    action: AuditCategory | null;
}

export function auditFiltersFromQuery(q: {
    period?: string | null;
    from?: string | null;
    to?: string | null;
    user?: string | null;
    action?: string | null;
}): AuditFilters {
    return {
        period: periodFromQuery(q.period, q.from, q.to),
        user: q.user?.trim() || null,
        action: isAuditCategory(q.action) ? q.action : null,
    };
}

export function auditFiltersToQuery(f: AuditFilters): Record<string, string | null> {
    return { ...periodToQuery(f.period), user: f.user, action: f.action };
}

/** `GET /api/admin/audit` params (without `cursor`/`limit`). */
export function auditApiParams(
    f: AuditFilters,
    range: { from: string | null; to: string | null },
): Record<string, string> {
    const params: Record<string, string> = {};
    if (range.from) params['from'] = range.from;
    if (range.to) params['to'] = range.to;
    if (f.user) params['user'] = f.user;
    if (f.action) params['action'] = auditCategoryCodes(f.action).join(',');
    return params;
}

export function auditFiltered(f: AuditFilters): boolean {
    return !!(f.period || f.user || f.action);
}

/** Row of `GET /api/admin/audit`. */
export interface AuditEvent {
    id: number | string;
    actor_user_id: string | number | null;
    effective_user_id: string | number | null;
    actor_login: string | null;
    effective_login: string | null;
    action: string;
    object_type: string | null;
    object_id: string | null;
    metadata_json: string | null;
    created_at: string;
}

/** Lookups for turning `object_type` + `object_id` into a readable name. */
export interface AuditObjectContext {
    users: ReadonlyMap<string, string>;
    tasks: ReadonlyMap<string, string>;
    queues: ReadonlyMap<string, string>;
    /** chat id or job id → request title. */
    requests: ReadonlyMap<string, string>;
}

const SETTINGS: Record<string, string> = { max_active_sessions: 'Общий лимит' };

/** «Объект» of an event: login, task title, «Очередь «…»», request title; the raw id when nothing matches. */
export function auditObjectLabel(e: Pick<AuditEvent, 'object_type' | 'object_id'>, ctx: AuditObjectContext): string {
    const id = e.object_id;
    if (!id) return '—';
    switch (e.object_type) {
        case 'user':
            return ctx.users.get(id) ?? id;
        case 'task':
            return ctx.tasks.get(id) ?? id;
        case 'queue': {
            const name = ctx.queues.get(id);
            return name ? `Очередь «${name}»` : id;
        }
        case 'setting':
            return SETTINGS[id] ?? id;
        default:
            return ctx.requests.get(id) ?? id;
    }
}

/** Pretty JSON of `metadata_json`; null when there are no details. */
export function auditMetadata(raw: string | null): string | null {
    if (!raw) return null;
    try {
        const value = JSON.parse(raw) as unknown;
        if (value === null || (typeof value === 'object' && Object.keys(value as object).length === 0)) return null;
        return JSON.stringify(value, null, 2);
    } catch {
        return raw.trim() || null;
    }
}
