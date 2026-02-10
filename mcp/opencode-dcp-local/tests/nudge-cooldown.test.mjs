import assert from 'node:assert/strict';
import { test, after } from 'node:test';
import { readFile, writeFile, unlink } from 'node:fs/promises';

// Exercise the actual shipped bundle, exposing internals only in a temporary copy.
const input = new URL(process.env.DCP_TEST_UPSTREAM ? '../upstream/index.js' : '../dist/index.js', import.meta.url);
const instrumented = new URL(`../dist/.nudge-test-${process.pid}.mjs`, import.meta.url);
await writeFile(instrumented, await readFile(input, 'utf8') + '\nexport { injectCompressNudges, createSessionState, defaultConfig };\n');
const { injectCompressNudges, createSessionState, defaultConfig } = await import(instrumented.href);
after(() => unlink(instrumented));
const logger = { info() {}, debug() {}, warn() {}, error() {} };
const prompts = Object.fromEntries(['contextLimitNudge', 'iterationNudge', 'turnNudge'].map(k => [k, `<dcp-system-reminder>${k}</dcp-system-reminder>`]));
const user = id => ({ info: { id, sessionID: 'test', time: { created: 1 }, role: 'user', model: { providerID: 'test', modelID: 'model' } }, parts: [{ type: 'text', text: 'Work on the task' }] });
const assistant = (id, tokens = 600, status) => ({
    info: { id, sessionID: 'test', role: 'assistant', time: { created: 1 }, tokens: { input: tokens, output: 1 } },
    parts: status ? [{ type: 'tool', tool: 'compress', state: { status } }] : [{ type: 'text', text: 'Continue work' }],
});
function fixture(afterCompression = 1, tokens = 600, status = 'completed') {
    const config = structuredClone(defaultConfig);
    Object.assign(config.compress, { minContextLimit: '50%', maxContextLimit: '80%', summaryBuffer: false });
    const state = createSessionState();
    state.modelContextLimit = 1000;
    const messages = [user('u'), ...Array.from({ length: 20 }, (_, i) => assistant(`old${i}`, tokens)), assistant('compressed', tokens, status), ...Array.from({ length: afterCompression }, (_, i) => assistant(`new${i}`, tokens))];
    return { config, state, messages };
}
function run(f, createAnchors = true) {
    const messages = structuredClone(f.messages);
    injectCompressNudges(f.state, f.config, logger, messages, prompts, undefined, createAnchors);
    return messages.flatMap(m => m.parts).map(p => p.text ?? '').join('\n').match(/<dcp-system-reminder>[\s\S]*?<\/dcp-system-reminder>/g)?.join('\n') ?? '';
}
test('does not remind again after one tool step following successful compression', () => {
    const f = fixture();
    assert.doesNotMatch(run(f), /dcp-system-reminder/);
});
test('iteration reminder restarts after 15 new messages, not 15 from original prompt', () => {
    assert.doesNotMatch(run(fixture(14)), /iterationNudge/);
    assert.match(run(fixture(15)), /iterationNudge/);
});
test('upper threshold respects the 5-message cooldown but still recovers large contexts', () => {
    assert.doesNotMatch(run(fixture(4, 900)), /contextLimitNudge/);
    assert.match(run(fixture(5, 900)), /contextLimitNudge/);
});
test('a later compression restarts cooldown again', () => {
    const f = fixture(15);
    assert.match(run(f), /iterationNudge/);
    f.messages.push(assistant('second', 600, 'completed'), assistant('after-second'));
    assert.doesNotMatch(run(f), /dcp-system-reminder/);
});
test('restored old anchors cannot replay reminders predating compression', () => {
    for (const createAnchors of [true, false]) {
        const f = fixture();
        for (const anchors of Object.values(f.state.nudges)) anchors.add('old19');
        assert.doesNotMatch(run(f, createAnchors), /dcp-system-reminder/);
    }
});
test('stale urgent warning disappears once token usage falls below upper threshold', () => {
    const f = fixture(5, 900);
    assert.match(run(f), /contextLimitNudge/);
    f.messages.push(assistant('lower-context', 600));
    assert.doesNotMatch(run(f), /contextLimitNudge/);
});
test('failed or pending compression does not reset the reminder clock', () => {
    for (const status of ['error', 'running', 'pending']) assert.match(run(fixture(1, 600, status)), /iterationNudge/);
});
test('normal initial reminder and frequency still work without compression', () => {
    const f = fixture();
    f.messages = [user('u'), ...Array.from({ length: 15 }, (_, i) => assistant(`initial${i}`))];
    assert.match(run(f), /iterationNudge/);
    const firstSize = f.state.nudges.iterationNudgeAnchors.size;
    f.messages.push(assistant('extra'));
    run(f);
    assert.equal(f.state.nudges.iterationNudgeAnchors.size, firstSize);
    for (let i = 0; i < 4; i++) f.messages.push(assistant(`extra${i}`));
    run(f);
    assert.equal(f.state.nudges.iterationNudgeAnchors.size, firstSize + 1);
});
test('new user prompt restarts ordinary iteration counting', () => {
    const f = fixture(15);
    f.messages.push(user('new-user'), assistant('reply'));
    assert.doesNotMatch(run(f), /iterationNudge/);
});
test('below minimum, manual mode and denied permission do not inject reminders', () => {
    assert.doesNotMatch(run(fixture(20, 100)), /dcp-system-reminder/);
    const manual = fixture(20); manual.state.manualMode = true;
    assert.doesNotMatch(run(manual), /dcp-system-reminder/);
    const denied = fixture(20); denied.config.compress.permission = 'deny';
    assert.doesNotMatch(run(denied), /dcp-system-reminder/);
});
test('ignored synthetic user messages do not consume the cooldown', () => {
    const f = fixture(4, 900);
    f.messages.push({ ...user('ignored'), parts: [{ type: 'text', text: 'synthetic', ignored: true }] });
    assert.doesNotMatch(run(f), /contextLimitNudge/);
});
