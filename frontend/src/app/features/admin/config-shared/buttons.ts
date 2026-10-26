/**
 * Compact button (prototype `k-btn--compact`, 24px): Koobiq 20 has
 * none, so it is a local class on `kbq-button`, added to a component's `styles`. The error-fade variant is the global `.v-reject`.
 */
export const BUTTON_CSS = `
    .v-compact {
        --kbq-button-size-height: 24px;
        --kbq-button-size-horizontal-padding: var(--kbq-size-s);
        --kbq-button-size-border-radius: 6px;
        min-height: 24px;
        /* 24px high, body-size text: the prototype's compact buttons render at 14px. */
        font-size: var(--kbq-typography-text-normal-font-size);
        line-height: 20px;
    }
`;
