import { ChangeDetectionStrategy, Component, input, output } from '@angular/core';
import { KbqButtonModule } from '@koobiq/components/button';
import { KbqComponentColors, PopUpPlacements } from '@koobiq/components/core';
import { KbqIconModule } from '@koobiq/components/icon';
import { KbqToolTipModule } from '@koobiq/components/tooltip';
import { sendShortcutHint } from './async-send';

/**
 * Primary send button of a text field (pattern «Асинхронное действие без формы»).
 * Disabled with a reason in the tooltip, or in progress while the request is in flight.
 * When enabled, the tooltip shows the label and the `Ctrl + Enter` / `⌘ Enter` hint on the second line.
 */
@Component({
    selector: 'v-send-button',
    imports: [KbqButtonModule, KbqIconModule, KbqToolTipModule],
    changeDetection: ChangeDetectionStrategy.OnPush,
    template: `
        <span
            class="v-send-button"
            [kbqTooltip]="reason() || tip"
            [kbqPlacement]="placements.Top"
            [kbqTooltipDisabled]="busy()"
        >
            <button
                kbq-button
                [color]="colors.Theme"
                [disabled]="!!reason() || busy()"
                [class.kbq-progress]="busy()"
                [attr.aria-busy]="busy()"
                [attr.aria-label]="label()"
                (click)="send.emit()"
            >
                @if (icon(); as name) {
                    <i [kbq-icon]="name"></i>
                }
                {{ label() }}
            </button>
        </span>
        <ng-template #tip>
            <div>{{ label() }}</div>
            <div class="v-send-button__hint">{{ hint }}</div>
        </ng-template>
    `,
    styles: `
        :host { display: inline-flex; }
        .v-send-button { display: inline-flex; }
        .v-send-button__hint { color: var(--kbq-foreground-contrast-secondary); }
    `,
})
export class SendButtonComponent {
    readonly label = input.required<string>();
    /** Why sending is not possible; shown in the tooltip. Empty = enabled. */
    readonly reason = input('');
    readonly busy = input(false);
    /** Optional leading icon class, e.g. `kbq-play_16`. */
    readonly icon = input('');
    readonly send = output<void>();

    protected readonly colors = KbqComponentColors;
    protected readonly placements = PopUpPlacements;
    protected readonly hint = sendShortcutHint();
}
