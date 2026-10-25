/**
 * Koobiq 20 has no filled theme button, so `[color]="theme"` on a filled `kbq-button` falls back to the UA style.
 * The prototype primary button (`.k-btn--primary`) is the theme fill with white text; add this to a component's
 * `styles` to get it for the buttons in that component's template.
 */
export const PRIMARY_BUTTON_CSS = `
    .kbq-button.kbq-button_filled.kbq-theme {
        border-color: transparent;
        background: var(--kbq-background-theme);
        color: var(--kbq-foreground-white);
    }
    .kbq-button.kbq-button_filled.kbq-theme .kbq-icon { color: var(--kbq-icon-white); }
    .kbq-button.kbq-button_filled.kbq-theme:hover:not(.kbq-disabled):not([disabled]) { background: var(--kbq-states-background-theme-hover); }
    .kbq-button.kbq-button_filled.kbq-theme:active:not(.kbq-disabled):not([disabled]) { background: var(--kbq-states-background-theme-active); }
    .kbq-button.kbq-button_filled.kbq-theme[disabled],
    .kbq-button.kbq-button_filled.kbq-theme.kbq-disabled {
        background: var(--kbq-states-background-disabled);
        color: var(--kbq-states-foreground-disabled);
    }
    .kbq-button.kbq-button_filled.kbq-theme[disabled] .kbq-icon,
    .kbq-button.kbq-button_filled.kbq-theme.kbq-disabled .kbq-icon { color: var(--kbq-states-icon-disabled); }
`;
