/**
 * Old Mattermost notification links point to hash routes of the previous UI.
 * Returns the new path for a known hash, or null.
 */
export function legacyHashTarget(hash: string): string | null {
    const m = /^#\/(consultations|tasks\/runs)\/([^/?#]+)\/?(\?.*)?$/.exec(hash);
    if (!m) return null;
    let id: string;
    try {
        id = encodeURIComponent(decodeURIComponent(m[2]));
    } catch {
        return null;
    }
    return m[1] === 'consultations' ? `/questions/${id}` : `/tasks/runs/${id}`;
}

/** Rewrites the address before the router starts, so the first navigation already uses the new route. */
export function rewriteLegacyHash(loc: Location = window.location, hist: History = window.history): void {
    const target = legacyHashTarget(loc.hash);
    if (target) hist.replaceState(hist.state, '', target);
}
