import { ChangeDetectionStrategy, Component, input } from '@angular/core';
import type { RunState } from './stats-math';

/** Mark shapes (prototype `SHAPE`): one per task type, «?» for questions. */
export type ShapeName = 'triangle' | 'square' | 'diamond' | 'down' | 'circle' | 'question';

/** Shapes handed to tasks in a fixed order; questions always get «?». */
export const TASK_SHAPES: ShapeName[] = ['triangle', 'square', 'diamond', 'down', 'circle'];

/**
 * A 12×12 glyph: `<svg vShape="triangle" st="done">`. Fill follows the run outcome via `--kbq-*` tokens:
 * done = theme, failed = error, cancelled = tertiary, running/queued = hollow.
 */
@Component({
    selector: 'svg[vShape]',
    changeDetection: ChangeDetectionStrategy.OnPush,
    host: {
        viewBox: '0 0 12 12',
        'aria-hidden': 'true',
        focusable: 'false',
        '[class]': '"v-shp v-st--" + st()',
    },
    template: `
        @switch (vShape()) {
            @case ('triangle') {
                <svg:polygon points="6,1 11.5,11 .5,11" />
            }
            @case ('square') {
                <svg:rect x="1.5" y="1.5" width="9" height="9" rx="1" />
            }
            @case ('diamond') {
                <svg:polygon points="6,.3 11.7,6 6,11.7 .3,6" />
            }
            @case ('down') {
                <svg:polygon points=".5,1 11.5,1 6,11" />
            }
            @case ('circle') {
                <svg:circle cx="6" cy="6" r="5" />
            }
            @case ('question') {
                <svg:text x="6" y="11" text-anchor="middle">?</svg:text>
            }
        }
    `,
    styles: `
        :host {
            width: 12px;
            height: 12px;
            display: inline-block;
            vertical-align: -1px;
            overflow: visible;
            flex: none;
            fill: currentColor;
            stroke: var(--kbq-background-bg);
            stroke-width: 1.5;
            paint-order: stroke;
        }
        text { font-size: 13px; font-weight: 800; font-family: inherit; stroke-width: 2.5; }
        :host(.v-st--done), :host(.v-st--running), :host(.v-st--queued) { color: var(--kbq-background-theme); }
        :host(.v-st--failed) { color: var(--kbq-background-error); }
        :host(.v-st--cancelled) { color: var(--kbq-foreground-contrast-tertiary); }
        :host(.v-st--running), :host(.v-st--queued) { fill: var(--kbq-background-bg); stroke: currentColor; stroke-width: 1.5; }
        :host(.v-st--running) text, :host(.v-st--queued) text { fill: currentColor; stroke: none; }
        :host(.v-st--key) { color: var(--kbq-foreground-contrast-secondary); }
    `,
})
export class ShapeComponent {
    readonly vShape = input.required<ShapeName>();
    readonly st = input<RunState | 'key'>('done');
}
