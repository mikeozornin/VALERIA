# DCP 3.2.0 local cooldown fix

This is an independent copy of the installed `@tarquinen/opencode-dcp` 3.2.0
package and its installed dependencies. No cache symlink is required. Upstream:
https://github.com/Opencode-DCP/opencode-dynamic-context-pruning

## Behavior

- Successful `compress` calls reset ordinary iteration reminders. The default
  threshold is 15 new messages after the later of the user prompt or compression.
- All automatic reminders wait at least `compress.nudgeFrequency` new messages
  after compression (default 5), including upper-context warnings.
- Anchors at or before the last successful compression are discarded, including
  anchors restored from disk. Failed or pending compress calls do not reset time.
- An upper-context warning is removed once usage falls below its threshold.
- Manual mode, permissions, compression tools and existing thresholds are retained.

This fixes reminder scheduling. It does not forbid the model from voluntarily
calling `compress`, and does not change OpenCode's native compaction.

## Build and regression tests

```sh
cd /home/appuser/mcp/opencode-dcp-local
npm run build:local
npm run test:local
```

The published package does not include the original full build configuration.
`scripts/build-local.mjs` transpiles the edited injection module and replaces only
that section of `upstream/index.js`. All other bundled modules remain unchanged.
Tests load the resulting bundle using a temporary file with test-only exports.
`DCP_TEST_UPSTREAM=1 npm run test:local` runs the same regression tests against the
unmodified bundle; seven tests fail there. The unmodified source module is saved
as `upstream/inject.ts`; `nudge-cooldown.patch` is the portable source diff.

## Activation and rollback

Global `/home/appuser/.config/opencode/opencode.jsonc` now references
`file:///home/appuser/mcp/opencode-dcp-local`. Global `dcp.jsonc` sets
`autoUpdate: false`. Restart OpenCode to load the fix into existing clients.

To return to upstream, replace just this plugin entry with
`@tarquinen/opencode-dcp@latest` and remove the `autoUpdate: false` override,
then restart OpenCode. Preserve any unrelated later config changes.

Before activation, back up both global files with a timestamped `.bak-dcp-cooldown-*`
suffix next to their originals.

The local fork does not receive upstream updates automatically. Rebase the source
patch and rerun the tests before switching to a newer upstream release.
