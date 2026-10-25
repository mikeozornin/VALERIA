import { afterNextRender, DestroyRef, Directive, ElementRef, inject, input, NgZone } from '@angular/core';
import { CpanelWidth } from '../../../shell/aside/panel-width';

/**
 * Saves the width the user dragged a content panel to. Put on `kbq-content-panel-header`: it exists only while
 * the panel is open, so the Koobiq wrapper (`.kbq-content-panel-container__panel`, rendered under `@if (opened)`)
 * is in the DOM when this starts observing it.
 *
 * Works around the shell `CpanelWidthDirective`, which looks up the wrapper once in a microtask, before the
 * container has rendered it, and never observes anything.
 */
@Directive({ selector: '[vAdminCpanelSave]' })
export class AdminCpanelSaveDirective {
    readonly panel = input.required<CpanelWidth>({ alias: 'vAdminCpanelSave' });

    constructor() {
        const host = inject<ElementRef<HTMLElement>>(ElementRef).nativeElement;
        const zone = inject(NgZone);
        let timer: ReturnType<typeof setTimeout> | null = null;
        let observer: ResizeObserver | null = null;

        afterNextRender(() => {
            const target = host.closest<HTMLElement>('.kbq-content-panel-container__panel');
            if (!target || typeof ResizeObserver === 'undefined') return;
            zone.runOutsideAngular(() => {
                observer = new ResizeObserver(() => {
                    const observed = Math.round(target.getBoundingClientRect().width);
                    if (timer) clearTimeout(timer);
                    timer = setTimeout(() => {
                        const panel = this.panel();
                        if (panel.squeezed() || observed === panel.width() || observed <= 0) return;
                        zone.run(() => panel.save(observed));
                    }, 300);
                });
                observer.observe(target);
            });
        });

        inject(DestroyRef).onDestroy(() => {
            observer?.disconnect();
            if (timer) clearTimeout(timer);
        });
    }
}
