import { MarkedOptions, Renderer, Tokens } from 'marked';

/** Hrefs we render as links: web, mail and relative/anchor links. Anything else (javascript:, data:, vbscript:) is text. */
const SAFE_HREF = /^(https?:|mailto:|\/|#|\.{0,2}\/)/i;

export function escapeHtml(value: string): string {
    return value
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

export function isSafeHref(href: string | null | undefined): boolean {
    if (!href) return false;
    // Browsers ignore control chars and whitespace inside the scheme (`java\tscript:`), so strip them before checking.
    // eslint-disable-next-line no-control-regex
    const compact = href.replace(/[\u0000- \u007f]/g, '');
    if (!/^[a-z][a-z0-9+.-]*:/i.test(compact)) return true; // no scheme: relative link
    return SAFE_HREF.test(compact);
}

/**
 * Renderer for agent markdown. `kbq-markdown` trusts whatever `marked` returns (`bypassSecurityTrustHtml`), so raw
 * HTML from the agent is shown as text, unsafe link schemes become plain text and images become their alt text.
 */
export function createSafeRenderer(): Renderer {
    const renderer = new Renderer();
    const baseLink = renderer.link.bind(renderer);
    renderer.html = ({ text }: Tokens.HTML | Tokens.Tag) => escapeHtml(text);
    renderer.link = (token: Tokens.Link) => {
        if (!isSafeHref(token.href)) return renderer.parser.parseInline(token.tokens);
        return baseLink(token);
    };
    renderer.image = ({ text }: Tokens.Image) => escapeHtml(text);
    return renderer;
}

export const SAFE_MARKED_OPTIONS: MarkedOptions = { gfm: true, breaks: false, renderer: createSafeRenderer() };
