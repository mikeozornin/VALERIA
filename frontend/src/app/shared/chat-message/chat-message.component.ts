import { ChangeDetectionStrategy, Component, inject, input } from '@angular/core';
import { KbqButtonModule } from '@koobiq/components/button';
import { KbqComponentColors } from '@koobiq/components/core';
import { KbqIconModule } from '@koobiq/components/icon';
import { KbqProgressSpinnerModule } from '@koobiq/components/progress-spinner';
import { NotifyService } from '../../core/notify/notify.service';

/** Clock time of a chat message: «14:16». */
export function messageTime(value: string | number | null | undefined): string {
    if (value === null || value === undefined || value === '') return '';
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return '';
    return date.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
}

/** The user's own message: right-aligned bubble and time (prototype `.v-msg--me`). */
@Component({
    selector: 'v-user-message',
    changeDetection: ChangeDetectionStrategy.OnPush,
    template: `
        <div class="v-msg__bubble">{{ text() }}</div>
        @if (time()) {
            <div class="v-msg__meta">{{ time() }}</div>
        }
    `,
    styles: `
        :host { display: flex; flex-direction: column; align-items: flex-end; margin: var(--kbq-size-l) 0; }
        .v-msg__bubble {
            max-width: 80%;
            box-sizing: border-box;
            padding: 10px 14px;
            border-radius: 12px 12px 4px 12px;
            background: var(--kbq-background-theme-less);
            white-space: pre-wrap;
            overflow-wrap: anywhere;
        }
        .v-msg__meta {
            margin-top: var(--kbq-size-xxs);
            font-size: 12px;
            line-height: 16px;
            color: var(--kbq-foreground-contrast-tertiary);
        }
    `,
})
export class UserMessageComponent {
    readonly text = input.required<string>();
    readonly time = input('');
}

/**
 * Valeria's message (prototype `.v-msg`): «Валерия» with the logo, projected content, then time and an optional
 * «Копировать ответ» that copies `copyText`.
 */
@Component({
    selector: 'v-bot-message',
    imports: [KbqButtonModule, KbqIconModule],
    changeDetection: ChangeDetectionStrategy.OnPush,
    template: `
        <div class="v-msg__who"><span class="v-logo" aria-hidden="true"></span>Валерия</div>
        <ng-content />
        @if (time() || copyText()) {
            <div class="v-msg__actions">
                @if (time()) {
                    <span class="v-msg__time">{{ time() }}</span>
                }
                @if (copyText()) {
                    <button kbq-button kbqStyle="transparent" [color]="colors.Contrast" class="v-copy-btn" (click)="copy()">
                        <i kbq-icon="kbq-square-multiple-o_16"></i>
                        Копировать ответ
                    </button>
                }
            </div>
        }
    `,
    styles: `
        :host { display: block; margin: var(--kbq-size-l) 0; }
        .v-msg__who {
            display: flex;
            align-items: center;
            gap: 6px;
            margin-bottom: 6px;
            font-weight: 600;
        }
        .v-logo {
            display: inline-block;
            flex: none;
            width: 20px;
            height: 20px;
            border-radius: 6px;
            background: var(--kbq-background-theme);
        }
        .v-msg__actions {
            display: flex;
            align-items: center;
            gap: var(--kbq-size-m);
            min-height: 24px;
            margin-top: var(--kbq-size-s);
        }
        .v-copy-btn.kbq-button {
            --kbq-button-size-height: 24px;
            --kbq-button-size-horizontal-padding: var(--kbq-size-s);
            --kbq-button-size-border-radius: 6px;
            font-size: 12px;
            line-height: 16px;
        }
        .v-msg__time { font-size: 12px; line-height: 16px; color: var(--kbq-foreground-contrast-tertiary); }
    `,
})
export class BotMessageComponent {
    readonly time = input('');
    /** Text put on the clipboard by «Копировать ответ»; no button when empty. */
    readonly copyText = input('');

    private readonly notify = inject(NotifyService);
    protected readonly colors = KbqComponentColors;

    protected async copy(): Promise<void> {
        try {
            await navigator.clipboard.writeText(this.copyText());
            this.notify.info('Скопировано');
        } catch {
            this.notify.error('Не удалось скопировать ответ');
        }
    }
}

/** Progress line with a spinner (prototype `.v-status`): «Валерия готовит ответ / Обычно 1–3 минуты.». */
@Component({
    selector: 'v-status-line',
    imports: [KbqProgressSpinnerModule],
    changeDetection: ChangeDetectionStrategy.OnPush,
    host: { role: 'status' },
    template: `
        <kbq-progress-spinner mode="indeterminate" class="v-status__spinner" />
        <div>
            <div>{{ text() }}</div>
            @if (sub()) {
                <div class="v-status__sub">{{ sub() }}</div>
            }
        </div>
    `,
    styles: `
        :host { display: flex; gap: var(--kbq-size-m); align-items: flex-start; padding: var(--kbq-size-s) 0; color: var(--kbq-foreground-contrast); }
        .v-status__spinner { flex: none; margin-top: 2px; }
        .v-status__sub { color: var(--kbq-foreground-contrast-secondary); }
    `,
})
export class StatusLineComponent {
    readonly text = input.required<string>();
    readonly sub = input('');
}
