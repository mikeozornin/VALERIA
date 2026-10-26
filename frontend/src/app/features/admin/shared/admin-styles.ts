/**
 * Layout pieces shared by the admin people screens (prototype `.app-content`, `.k-table` rows, `.v-form`,
 * `.v-cdl`, `.v-csec`). Colours only from `--kbq-*` tokens.
 */
export const ADMIN_LIST_STYLES = `
    :host { display: flex; flex-direction: column; flex: 1 1 auto; min-height: 0; min-width: 0; }
    .v-body { position: relative; display: flex; flex: 1 1 auto; min-height: 0; min-width: 0; }
    .v-body > kbq-content-panel-container { flex: 1 1 auto; min-width: 0; }
    .v-content { height: 100%; overflow: auto; box-sizing: border-box; padding: 16px 24px 24px; }
    .v-filters { display: flex; align-items: center; gap: 12px; }
    .v-filters > kbq-filter-bar { flex: 1 1 0; min-width: 0; }
    .v-filters > v-admin-search { flex: none; }
    .v-table { width: 100%; }
    .v-table tbody tr { cursor: pointer; }
    .v-table tbody tr:focus-visible { outline: 2px solid var(--kbq-states-line-focus-theme); outline-offset: -2px; }
    .v-table tbody tr.v-selected td { background: var(--kbq-background-theme-less); }
    .v-table .v-sub { display: block; font-size: 12px; line-height: 16px; color: var(--kbq-foreground-contrast-secondary); }
    .v-table .v-nowrap { white-space: nowrap; }
    .v-table .v-check { width: 32px; padding-right: 0; }
    .v-secondary { color: var(--kbq-foreground-contrast-secondary); }
    .v-tertiary { color: var(--kbq-foreground-contrast-tertiary); }
    .v-mono { font-family: var(--kbq-font-family-mono, monospace); }
    .v-empty { margin-top: 80px; }
    .v-dl { row-gap: 8px; }
    .v-break { word-break: break-all; }
    .v-csec { margin-top: 24px; }
    .v-csec__title { font-weight: 600; margin-bottom: 8px; }
    .v-csec__title--row { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
    .v-panel-acts { display: flex; gap: 8px; margin-bottom: 16px; }
    .v-panel-title { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
`;

export const ADMIN_FORM_STYLES = `
    :host { display: flex; flex-direction: column; flex: 1 1 auto; min-height: 0; min-width: 0; }
    .v-formscroll { flex: 1 1 auto; min-height: 0; overflow: auto; display: flex; flex-direction: column; padding: 16px 24px 0; }
    .v-form { max-width: 880px; display: flex; flex-direction: column; gap: 32px; padding-bottom: 24px; }
    .v-form > kbq-alert { margin-bottom: -8px; }
    .v-fsec { display: flex; flex-direction: column; gap: 16px; border: 0; padding: 0; margin: 0; }
    .v-fsec__title { font-size: 16px; line-height: 24px; font-weight: 600; padding: 0; margin: 0 0 4px; }
    .v-frow { display: grid; grid-template-columns: 200px minmax(0, 1fr); gap: 16px; align-items: start; }
    .v-frow__label { padding-top: 6px; color: var(--kbq-foreground-contrast-secondary); }
    .v-frow__label .v-req { color: var(--kbq-foreground-error); }
    .v-frow__ctl { display: flex; flex-direction: column; gap: 4px; min-width: 0; }
    .v-frow__ctl kbq-form-field { width: 100%; }
    .v-w320 { width: 320px !important; max-width: 100%; }
    .v-w160 { width: 160px !important; }
    .v-check-group { display: flex; flex-direction: column; gap: 8px; padding-top: 6px; }
    .v-check-sub { display: block; font-size: 12px; line-height: 16px; color: var(--kbq-foreground-contrast-secondary); }
    .v-hint { font-size: 12px; line-height: 16px; color: var(--kbq-foreground-contrast-secondary); }
    .v-mono input, input.v-mono { font-family: var(--kbq-font-family-mono, monospace); }
    .v-formfoot {
        position: sticky; bottom: 0; z-index: 5; margin: auto -24px 0; padding: 12px 24px;
        display: flex; align-items: center; gap: 8px;
        background: var(--kbq-background-bg); border-top: 1px solid var(--kbq-line-contrast-less);
        box-shadow: var(--kbq-shadow-card);
    }
    .v-pt6 { padding-top: 6px; }
`;
