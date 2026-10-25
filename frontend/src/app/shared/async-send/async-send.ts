import { ApiError } from '../../core/api/api-error';
import { newIdempotencyKey } from '../../core/api/api.service';
import { describeError } from '../../core/status/error-codes';

/**
 * Idempotency-Key for one user action (SPEC §7): created on the click, reused when the user retries the same
 * payload after a network failure, dropped after any answer from the server or when the payload changes.
 */
export class RetryKey {
    private key: string | null = null;
    private payload: string | null = null;

    take(payload: string): string {
        if (!this.key || this.payload !== payload) {
            this.key = newIdempotencyKey();
            this.payload = payload;
        }
        return this.key;
    }

    /** Call after the request settles. Keeps the key only for a network failure (the server may not have seen it). */
    settle(error?: unknown): void {
        if (error instanceof ApiError && error.isNetwork) return;
        this.key = null;
        this.payload = null;
    }
}

/** «Не удалось запустить задачу: сервер недоступен» (SPEC §7). */
export function failureText(action: string, error: unknown): string {
    const { text } = describeError(error);
    const reason = text ? text.charAt(0).toLocaleLowerCase() + text.slice(1) : '';
    return reason ? `${action}: ${reason.replace(/\.$/, '')}` : action;
}

export function isMacPlatform(): boolean {
    if (typeof navigator === 'undefined') return false;
    const nav = navigator as Navigator & { userAgentData?: { platform?: string } };
    const platform = nav.userAgentData?.platform || nav.platform || nav.userAgent || '';
    return /mac|iphone|ipad|ipod/i.test(platform);
}

/** Second tooltip line of a send button (SPEC §11, keyboard shortcuts pattern). */
export function sendShortcutHint(): string {
    return isMacPlatform() ? '⌘ Enter' : 'Ctrl + Enter';
}

/** `Ctrl + Enter` / `⌘ Enter` in a text field. */
export function isSendShortcut(event: KeyboardEvent): boolean {
    return event.key === 'Enter' && (event.metaKey || event.ctrlKey) && !event.isComposing;
}
