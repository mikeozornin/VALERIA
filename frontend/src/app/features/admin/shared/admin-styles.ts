/**
 * Layout pieces shared by the admin people screens (list + detail panel, form wrapper).
 * Only layout and `--kbq-*` tokens: Koobiq components keep their stock look.
 */
export const ADMIN_LIST_STYLES = `
    :host { display: flex; flex-direction: column; flex: 1 1 auto; min-height: 0; min-width: 0; }
    .v-body { position: relative; display: flex; flex: 1 1 auto; min-height: 0; min-width: 0; }
    .v-body > kbq-content-panel-container { flex: 1 1 auto; min-width: 0; }
    /* Koobiq sizes the resizable wrapper, but the panel inside it has no flex-grow and would take its content width. */
    .v-body kbq-content-panel { flex: 1 1 auto; min-width: 0; }
    .v-content { height: 100%; overflow: auto; box-sizing: border-box; padding: var(--kbq-size-l) var(--kbq-size-xxl) var(--kbq-size-xxl); }
    /* Prototype filterBar(): pipes, «Сбросить», then the search pushed right; it wraps when the row is full. */
    .v-filters { display: flex; flex-wrap: wrap; align-items: center; gap: var(--kbq-size-s); }
    .v-filters > v-admin-search { flex: none; }
    .v-filters > kbq-filter-bar ~ v-admin-search { margin-left: auto; }
    .v-table { width: 100%; }
    .v-table tbody tr { cursor: pointer; }
    /* kbq-table has no keyboard-focus or selected-row state: rows open the detail panel. */
    .v-table tbody tr:focus-visible { outline: 2px solid var(--kbq-states-line-focus-theme); outline-offset: -2px; }
    .v-table tbody tr.v-selected td { background: var(--kbq-background-theme-less); }
    .v-table .v-sub {
        display: block;
        font-size: var(--kbq-typography-text-compact-font-size);
        line-height: var(--kbq-typography-text-compact-line-height);
        letter-spacing: var(--kbq-typography-text-compact-letter-spacing);
        color: var(--kbq-foreground-contrast-secondary);
    }
    .v-table .v-nowrap { white-space: nowrap; }
    .v-table .v-check { width: var(--kbq-size-3xl); padding-right: 0; }
    .v-secondary { color: var(--kbq-foreground-contrast-secondary); }
    .v-tertiary { color: var(--kbq-foreground-contrast-tertiary); }
    .v-mono { font-family: var(--kbq-typography-mono-normal-font-family); }
    .v-empty { margin-top: 80px; }
    .v-break { word-break: break-all; }
    .v-csec { margin-top: var(--kbq-size-xxl); }
    .v-csec__title {
        margin-bottom: var(--kbq-size-s);
        font-family: var(--kbq-typography-text-normal-strong-font-family);
        font-size: var(--kbq-typography-text-normal-strong-font-size);
        line-height: var(--kbq-typography-text-normal-strong-line-height);
        font-weight: var(--kbq-typography-text-normal-strong-font-weight);
        letter-spacing: var(--kbq-typography-text-normal-strong-letter-spacing);
    }
    .v-csec__title--row { display: flex; align-items: center; justify-content: space-between; gap: var(--kbq-size-s); }
    .v-panel-acts { display: flex; gap: var(--kbq-size-s); margin-bottom: var(--kbq-size-l); }
    .v-panel-title { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .v-panel-title.v-mono {
        font-family: var(--kbq-typography-mono-normal-font-family);
        font-size: var(--kbq-typography-mono-normal-font-size);
        line-height: var(--kbq-typography-mono-normal-line-height);
        letter-spacing: var(--kbq-typography-mono-normal-letter-spacing);
    }
`;

/** User forms: sections, rows and the footer come from `config-shared/form-layout` (`v-form-page`, `vFsec`, `v-frow`). */
export const ADMIN_FORM_STYLES = `
    :host { display: flex; flex-direction: column; flex: 1 1 auto; min-height: 0; min-width: 0; }
    .v-formwrap { display: flex; flex-direction: column; flex: 1 1 auto; min-height: 0; }
    .v-w320 { width: 320px; max-width: 100%; }
    .v-w160 { width: 160px; }
    .v-check-group { display: flex; flex-direction: column; gap: var(--kbq-size-s); padding-top: var(--kbq-size-xs); }
`;
