import { requestFilterParams } from '../requests/requests.filters';
import {
    auditApiParams,
    auditFiltered,
    auditFiltersFromQuery,
    auditFiltersToQuery,
    auditMetadata,
    AuditObjectContext,
    auditObjectLabel,
} from './audit.filters';

const ctx: AuditObjectContext = {
    users: new Map([['u-1', 'a.nikitina']]),
    tasks: new Map([['t-ci', 'Разбор упавшего CI-пайплайна']]),
    queues: new Map([['questions', 'Вопросы']]),
    requests: new Map([['q-1', 'Как собрать прошивку?']]),
};

describe('audit filters ↔ query', () => {
    it('round-trips filters through the URL', () => {
        const f = auditFiltersFromQuery({ period: '7d', user: 'd.orlov', action: 'users' });
        expect(f).toEqual({ period: { code: '7d' }, user: 'd.orlov', action: 'users' });
        expect(auditFiltersToQuery(f)).toEqual({
            period: '7d',
            from: null,
            to: null,
            user: 'd.orlov',
            action: 'users',
        });
        expect(auditFiltered(f)).toBe(true);
    });

    it('ignores unknown categories and empty values', () => {
        const f = auditFiltersFromQuery({ period: 'nope', user: '  ', action: 'bogus' });
        expect(f).toEqual({ period: null, user: null, action: null });
        expect(auditFiltered(f)).toBe(false);
        expect(auditFiltersToQuery(f)).toEqual({ period: null, from: null, to: null, user: null, action: null });
    });

    it('keeps a custom range', () => {
        const f = auditFiltersFromQuery({ period: 'custom', from: '2026-10-01T00:00:00Z', to: '2026-10-02T00:00:00Z' });
        expect(auditFiltersToQuery(f)).toEqual(
            expect.objectContaining({ period: 'custom', from: '2026-10-01T00:00:00Z', to: '2026-10-02T00:00:00Z' }),
        );
    });

    it('builds API params with category codes', () => {
        const params = auditApiParams(
            { period: null, user: 'p.volkov', action: 'auth' },
            { from: '2026-10-18T00:00:00.000Z', to: null },
        );
        expect(params).toEqual({ from: '2026-10-18T00:00:00.000Z', user: 'p.volkov', action: 'auth.login' });
        expect(auditApiParams({ period: null, user: null, action: null }, { from: null, to: null })).toEqual({});
    });
});

describe('audit object and metadata', () => {
    it('resolves objects by type, falls back to the raw id', () => {
        expect(auditObjectLabel({ object_type: 'user', object_id: 'u-1' }, ctx)).toBe('a.nikitina');
        expect(auditObjectLabel({ object_type: 'task', object_id: 't-ci' }, ctx)).toBe('Разбор упавшего CI-пайплайна');
        expect(auditObjectLabel({ object_type: 'queue', object_id: 'questions' }, ctx)).toBe('Очередь «Вопросы»');
        expect(auditObjectLabel({ object_type: 'setting', object_id: 'max_active_sessions' }, ctx)).toBe('Общий лимит');
        expect(auditObjectLabel({ object_type: 'consultation', object_id: 'q-1' }, ctx)).toBe('Как собрать прошивку?');
        expect(auditObjectLabel({ object_type: 'permission', object_id: 'per_7Hq2' }, ctx)).toBe('per_7Hq2');
        expect(auditObjectLabel({ object_type: null, object_id: null }, ctx)).toBe('—');
    });

    it('pretty-prints metadata, empty object means no details', () => {
        expect(auditMetadata('{"value":4}')).toBe('{\n  "value": 4\n}');
        expect(auditMetadata('{}')).toBeNull();
        expect(auditMetadata(null)).toBeNull();
        expect(auditMetadata('not json')).toBe('not json');
    });
});

describe('requests filter ↔ API params', () => {
    it('joins lists and drops empty filters', () => {
        expect(
            requestFilterParams(
                { users: ['a.nikitina', 'p.volkov'], period: null, types: ['task', 'question'], task: 't-ci' },
                { from: '2026-10-18T00:00:00.000Z', to: null },
            ),
        ).toEqual({
            user: 'a.nikitina,p.volkov',
            type: 'task,question',
            task: 't-ci',
            from: '2026-10-18T00:00:00.000Z',
        });
        expect(
            requestFilterParams({ users: [], period: null, types: [], task: null }, { from: null, to: null }),
        ).toEqual({});
    });

    it('drops the task when tasks are not among the types', () => {
        expect(
            requestFilterParams(
                { users: [], period: null, types: ['question'], task: 't-ci' },
                { from: null, to: null },
            ),
        ).toEqual({ type: 'question' });
    });
});
