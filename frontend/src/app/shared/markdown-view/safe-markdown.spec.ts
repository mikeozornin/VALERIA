import { marked } from 'marked';
import { isSafeHref, SAFE_MARKED_OPTIONS } from './safe-markdown';

const render = (md: string) => marked.parse(md, { ...SAFE_MARKED_OPTIONS, async: false }) as string;

describe('safe markdown renderer', () => {
    it('escapes block HTML such as <script>', () => {
        const html = render('<script>alert(1)</script>');
        expect(html).not.toContain('<script');
        expect(html).toContain('&lt;script&gt;');
    });

    it('escapes inline HTML such as <img onerror>', () => {
        const html = render('text <img src=x onerror="alert(1)"> more');
        expect(html).not.toContain('<img');
        expect(html).toContain('&lt;img');
    });

    it('drops javascript: links but keeps their text', () => {
        const html = render('[click](javascript:alert(1))');
        expect(html).not.toContain('href');
        expect(html).toContain('click');
    });

    it('keeps https links', () => {
        expect(render('[docs](https://example.com)')).toContain('<a href="https://example.com">docs</a>');
    });

    it('renders images as their alt text', () => {
        const html = render('![logo](https://evil.example/x.png)');
        expect(html).not.toContain('<img');
        expect(html).toContain('logo');
    });

    it('renders regular markdown', () => {
        expect(render('Use `foo`')).toContain('<code>foo</code>');
    });

    it.each([
        ['https://a.b', true],
        ['/relative', true],
        ['page#x', true],
        ['mailto:a@b.c', true],
        ['javascript:alert(1)', false],
        ['JAVA\tSCRIPT:alert(1)', false],
        ['data:text/html,x', false],
        ['vbscript:x', false],
    ])('isSafeHref(%s) = %s', (href, safe) => {
        expect(isSafeHref(href)).toBe(safe);
    });
});
