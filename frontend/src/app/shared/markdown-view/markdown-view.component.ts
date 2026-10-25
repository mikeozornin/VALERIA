import { ChangeDetectionStrategy, Component, input } from '@angular/core';
import { KbqMarkdownModule, kbqMarkdownMarkedOptionsProvider } from '@koobiq/components/markdown';
import { SAFE_MARKED_OPTIONS } from './safe-markdown';

/**
 * Agent answer rendered through `kbq-markdown` with the sanitizing renderer (raw HTML escaped, unsafe links dropped).
 * Spacing follows the prototype `.v-md`: body size, paragraphs and lists 0 0 8px, list items 4px, headings 14/600
 * with 16/6 margins. Used for chat answers and the task-run report alike.
 */
@Component({
    selector: 'v-markdown-view',
    imports: [KbqMarkdownModule],
    providers: [kbqMarkdownMarkedOptionsProvider(SAFE_MARKED_OPTIONS)],
    changeDetection: ChangeDetectionStrategy.OnPush,
    template: `
        @if (text()) {
            <kbq-markdown class="v-md" [markdownText]="text() ?? null" />
        }
    `,
    styles: `
        :host { display: block; min-width: 0; }
        .v-md {
            --kbq-markdown-size-max-width: none;
            --kbq-markdown-p-size-margin-top: 0;
            --kbq-markdown-p-size-margin-bottom: var(--kbq-size-s);
            --kbq-markdown-list-size-margin-top: 0;
            --kbq-markdown-list-size-margin-bottom: var(--kbq-size-s);
            --kbq-markdown-list-size-margin-top-after-paragraph: 0;
            --kbq-markdown-list-size-item-margin-bottom: var(--kbq-size-xxs);
            --kbq-markdown-list-size-ul-padding: 0 0 0 20px;
            --kbq-markdown-h4-size-margin-top: var(--kbq-size-l);
            --kbq-markdown-h4-size-margin-bottom: 6px;
            --kbq-markdown-code-size-inline-padding: 1px var(--kbq-size-xxs);
            --kbq-markdown-code-size-multiline-margin-top: 0;
            --kbq-markdown-code-size-multiline-margin-bottom: var(--kbq-size-s);
            overflow: visible;
        }
        .v-md ::ng-deep :is(.kbq-markdown__p, .kbq-markdown__ul, .kbq-markdown__ol) {
            font-size: var(--kbq-typography-text-normal-font-size);
            line-height: var(--kbq-typography-text-normal-line-height);
        }
        .v-md ::ng-deep :is(.kbq-markdown__h1, .kbq-markdown__h2, .kbq-markdown__h3, .kbq-markdown__h4, .kbq-markdown__h5, .kbq-markdown__h6) {
            margin: var(--kbq-size-l) 0 6px;
            font-size: var(--kbq-typography-text-normal-font-size);
            line-height: var(--kbq-typography-text-normal-line-height);
            font-weight: 600;
        }
        .v-md ::ng-deep :is(.kbq-markdown__h1, .kbq-markdown__h2, .kbq-markdown__h3, .kbq-markdown__h4, .kbq-markdown__h5, .kbq-markdown__h6):first-child {
            margin-top: 0;
        }
    `,
})
export class MarkdownViewComponent {
    readonly text = input<string | null | undefined>('');
}
