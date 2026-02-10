import ts from 'typescript';
import { readFile, writeFile } from 'node:fs/promises';

// The published package lacks its original build entry/config. Replace only the
// bundled inject module, using its edited TS source; leave every other module intact.
const root = new URL('../', import.meta.url);
const source = await readFile(new URL('lib/messages/inject/inject.ts', root), 'utf8');
const { outputText, diagnostics } = ts.transpileModule(source, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
    reportDiagnostics: true,
});
if (diagnostics?.length) throw new Error(ts.formatDiagnosticsWithColorAndContext(diagnostics, {
    getCanonicalFileName: x => x, getCurrentDirectory: () => process.cwd(), getNewLine: () => '\n',
}));
const ast = ts.createSourceFile('inject.js', outputText, ts.ScriptTarget.Latest, true, ts.ScriptKind.JS);
const body = ast.statements.filter(s => !ts.isImportDeclaration(s)).map(s => s.getText(ast).replace(/^export\s+/, '')).join('\n');
const original = await readFile(new URL('upstream/index.js', root), 'utf8');
const startMarker = '// lib/messages/inject/inject.ts\n';
const endMarker = '// lib/messages/inject/subagent-results.ts';
const start = original.indexOf(startMarker);
const end = original.indexOf(endMarker, start);
if (start < 0 || end < 0) throw new Error('Upstream bundle layout changed');
const built = original.slice(0, start) + startMarker + body + '\n\n' + original.slice(end);
await writeFile(new URL('dist/index.js', root), built.replace(/\/\/# sourceMappingURL=index\.js\.map\s*$/, ''));
console.log('Rebuilt inject module from local TypeScript source');
