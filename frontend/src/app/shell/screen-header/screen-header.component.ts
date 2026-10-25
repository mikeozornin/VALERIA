import { ChangeDetectionStrategy, Component, computed, inject, input } from '@angular/core';
import { RouterLink } from '@angular/router';
import { KbqButtonModule } from '@koobiq/components/button';
import { KbqComponentColors, PopUpPlacements } from '@koobiq/components/core';
import { KbqIconModule } from '@koobiq/components/icon';
import { KbqToolTipModule } from '@koobiq/components/tooltip';
import { KbqTopBarModule } from '@koobiq/components/top-bar';
import { Item } from '../../core/items/item.model';
import { UI_KEYS, UiStateService } from '../../core/state/ui-state.service';
import { ChatMenuComponent } from '../chat-menu/chat-menu.component';

export interface Crumb {
    label: string;
    link: string;
}

/**
 * Screen header over `kbq-top-bar` (SPEC §6.3).
 *
 * ```html
 * <v-screen-header title="Пользователи" [crumbs]="[{ label: 'Система', link: '/admin/statistics' }]">
 *   <span vHeaderLead><!-- mode icon or tag --></span>
 *   <ng-container vHeaderActions><!-- screen actions --></ng-container>
 * </v-screen-header>
 * ```
 * `[chat]` adds the chat menu «…»; `[asideToggle]="true"` adds «Скрыть панель» / «Показать панель»
 * bound to `valeria.chat.aside.open`.
 */
@Component({
    selector: 'v-screen-header',
    imports: [KbqTopBarModule, KbqButtonModule, KbqIconModule, KbqToolTipModule, RouterLink, ChatMenuComponent],
    changeDetection: ChangeDetectionStrategy.OnPush,
    host: { class: 'v-screen-header' },
    template: `
        <kbq-top-bar>
            <div kbqTopBarContainer placement="start" class="v-screen-header__start">
                <ng-content select="[vHeaderBack]" />
                @for (crumb of crumbs(); track crumb.link) {
                    <a class="kbq-title v-screen-header__crumb" [routerLink]="crumb.link">{{ crumb.label }}</a>
                    <i kbq-icon="kbq-chevron-right_16" [color]="colors.ContrastFade" aria-hidden="true"></i>
                }
                <ng-content select="[vHeaderLead]" />
                <h1 class="kbq-title v-screen-header__title">{{ title() }}</h1>
                <ng-content select="[vHeaderAfterTitle]" />
            </div>
            <div kbqTopBarSpacer></div>
            <div kbqTopBarContainer placement="end">
                <ng-content select="[vHeaderActions]" />
                @if (chat(); as item) {
                    <v-chat-menu [item]="item" appearance="header" />
                }
                @if (asideToggle()) {
                    <button
                        kbq-button
                        kbqStyle="transparent"
                        [color]="colors.Contrast"
                        [class.kbq-active]="asideOpen()"
                        [kbqTooltip]="asideLabel()"
                        [kbqPlacement]="placements.Bottom"
                        [attr.aria-label]="asideLabel()"
                        [attr.aria-pressed]="asideOpen()"
                        (click)="toggleAside()"
                    >
                        <i kbq-icon="kbq-split-screen-right_16"></i>
                    </button>
                }
            </div>
        </kbq-top-bar>
    `,
    styles: `
        :host { display: block; flex: none; border-bottom: 1px solid var(--kbq-line-contrast-less); }
        .v-screen-header__start { align-items: center; min-width: 0; }
        .v-screen-header__title {
            margin: 0;
            min-width: 0;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
        }
        .v-screen-header__crumb {
            flex: none;
            color: var(--kbq-foreground-contrast-secondary);
            text-decoration: none;
            white-space: nowrap;
        }
        .v-screen-header__crumb:hover { color: var(--kbq-foreground-contrast); }
    `,
})
export class ScreenHeaderComponent {
    readonly title = input('');
    readonly crumbs = input<Crumb[]>([]);
    /** Chat whose menu «…» is shown on the right. */
    readonly chat = input<Item | null | undefined>(null);
    /** Shows the «Скрыть панель» / «Показать панель» toggle for the chat aside. */
    readonly asideToggle = input(false);

    protected readonly colors = KbqComponentColors;
    protected readonly placements = PopUpPlacements;
    protected readonly asideOpen = inject(UiStateService).state(UI_KEYS.chatAsideOpen);
    protected readonly asideLabel = computed(() => (this.asideOpen() ? 'Скрыть панель' : 'Показать панель'));

    protected toggleAside(): void {
        this.asideOpen.update((open) => !open);
    }
}
