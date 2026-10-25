/**
 * Compact button (prototype `k-btn--compact`, 24px) and the error-tinted variant (`k-btn--error-fade`): Koobiq 20 has
 * neither, so they are local classes on `kbq-button`, added to a component's `styles`.
 */
export const BUTTON_CSS = `
    .v-compact {
        --kbq-button-size-height: 24px;
        --kbq-button-size-horizontal-padding: var(--kbq-size-s);
        --kbq-button-size-border-radius: 6px;
        min-height: 24px;
        font-size: var(--kbq-typography-text-compact-font-size);
        line-height: var(--kbq-typography-text-compact-line-height);
    }
    .v-error-fade.kbq-button:not([disabled]) {
        background: var(--kbq-background-error-fade);
        color: var(--kbq-foreground-error);
        border-color: transparent;
    }
`;
