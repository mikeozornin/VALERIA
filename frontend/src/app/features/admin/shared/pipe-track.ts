import { KbqPipe, KbqPipeTypes } from '@koobiq/components/filter-bar';

/**
 * `@for` track key for `*kbqPipe`. The Koobiq pipe reads its data once, when it is created, so a pipe whose value
 * is resolved later (a task title that arrives after the task list loads, for a `?task=` deep link) would keep the
 * empty or raw value. A single-select value's label is part of the key: when it changes, the pipe is re-created
 * with the new data. Multi-select and datetime pipes keep a stable key so an open multi-select is not closed while
 * the user picks values.
 */
export function pipeTrackKey(pipe: KbqPipe): string {
    const id = pipe.id ?? pipe.name;
    if (pipe.type !== KbqPipeTypes.Select) return String(id);
    const value = pipe.value as { name?: unknown } | null | undefined;
    return `${String(id)}|${value?.name ?? ''}`;
}
