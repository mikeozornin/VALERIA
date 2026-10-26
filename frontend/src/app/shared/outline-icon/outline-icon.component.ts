import { ChangeDetectionStrategy, Component, input } from '@angular/core';

export type OutlineIconName =
    | 'alert-circle'
    | 'alert-triangle'
    | 'help-circle'
    | 'check-circle'
    | 'x-circle'
    | 'clock'
    | 'lock'
    | 'eye'
    | 'info'
    | 'x'
    | 'chevron-down';

/**
 * Outline (stroke) icons from the prototype's sprite (`<symbol id="i-…">`, 24×24, stroke 1.75 at 16px).
 * Koobiq's icon font has only filled circles for these, so status marks, alert icons and card icons use this.
 * Colour comes from `currentColor`: set it with a `--kbq-icon-*` token on the host or a parent.
 *
 * ```html
 * <v-outline-icon name="alert-circle" class="v-ic_error" />
 * ```
 */
@Component({
    selector: 'v-outline-icon',
    changeDetection: ChangeDetectionStrategy.OnPush,
    host: { class: 'v-outline-icon', 'aria-hidden': 'true', '[class.v-outline-icon_24]': 'size() === 24' },
    template: `
        <svg viewBox="0 0 24 24" focusable="false">
            @switch (name()) {
                @case ('alert-circle') {
                    <circle cx="12" cy="12" r="10" />
                    <line x1="12" y1="8" x2="12" y2="12" />
                    <line x1="12" y1="16" x2="12.01" y2="16" />
                }
                @case ('alert-triangle') {
                    <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z" />
                    <line x1="12" y1="9" x2="12" y2="13" />
                    <line x1="12" y1="17" x2="12.01" y2="17" />
                }
                @case ('help-circle') {
                    <circle cx="12" cy="12" r="10" />
                    <path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3" />
                    <line x1="12" y1="17" x2="12.01" y2="17" />
                }
                @case ('check-circle') {
                    <path d="M22 11.08V12a10 10 0 1 1-5.93-9.14" />
                    <polyline points="22 4 12 14.01 9 11.01" />
                }
                @case ('x-circle') {
                    <circle cx="12" cy="12" r="10" />
                    <line x1="15" y1="9" x2="9" y2="15" />
                    <line x1="9" y1="9" x2="15" y2="15" />
                }
                @case ('clock') {
                    <circle cx="12" cy="12" r="10" />
                    <polyline points="12 6 12 12 16 14" />
                }
                @case ('lock') {
                    <rect x="3" y="11" width="18" height="11" rx="2" ry="2" />
                    <path d="M7 11V7a5 5 0 0 1 10 0v4" />
                }
                @case ('eye') {
                    <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z" />
                    <circle cx="12" cy="12" r="3" />
                }
                @case ('info') {
                    <circle cx="12" cy="12" r="10" />
                    <line x1="12" y1="16" x2="12" y2="12" />
                    <line x1="12" y1="8" x2="12.01" y2="8" />
                }
                @case ('x') {
                    <line x1="18" y1="6" x2="6" y2="18" />
                    <line x1="6" y1="6" x2="18" y2="18" />
                }
                @case ('chevron-down') {
                    <polyline points="6 9 12 15 18 9" />
                }
            }
        </svg>
    `,
    styles: `
        :host {
            display: inline-flex;
            flex: none;
            width: 16px;
            height: 16px;
            vertical-align: middle;
        }
        :host(.v-outline-icon_24) { width: 24px; height: 24px; }
        svg {
            width: 100%;
            height: 100%;
            fill: none;
            stroke: currentColor;
            stroke-width: 1.75;
            stroke-linecap: round;
            stroke-linejoin: round;
        }
        :host(.v-outline-icon_24) svg { stroke-width: 1.5; }
        :host(.v-ic_fade) { color: var(--kbq-icon-contrast-fade); }
        :host(.v-ic_theme) { color: var(--kbq-icon-theme); }
        :host(.v-ic_error) { color: var(--kbq-icon-error); }
        :host(.v-ic_success) { color: var(--kbq-icon-success); }
        :host(.v-ic_warning) { color: var(--kbq-icon-warning); }
    `,
})
export class OutlineIconComponent {
    readonly name = input.required<OutlineIconName>();
    readonly size = input<16 | 24>(16);
}
