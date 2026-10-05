# Discord Crash Decoder

A Python bot and local decoder for Metroid Prime and Echoes crash screens. Right-click a
message, choose **Apps → Decode crash**, and receive one reply. A single screenshot's trace
appears in the message body; a trace exceeding Discord's 2,000-character body limit becomes
an attachment. Multiple screenshots get separate `crash-trace-N.txt` attachments with no
screenshot list in the reply body. Each attached trace contains the screenshot name,
build, crash description, and function names with offsets and source files. Larger batches
use `crash-traces.zip` containing the individual traces to fit Discord's attachment limit.
The bot acknowledges privately before downloading or running OCR. Private controls
let the requester select a game/build, correct OCR text, and supply verified REL layout data.
Diagnostic JSON attachments are disabled by default. Set `ATTACH_DIAGNOSTICS=true` on a debug
instance to attach `crash-diagnostic.json` to replies and corrections. This runtime setting
applies to every decoded game build, including unknown builds and per-image errors. Detection
evidence, OCR corrections, and bundle checksums stay in diagnostic JSON. Unresolved addresses
and per-image errors remain visible in the text traces.
Stack lines show the source filename and a REL name when applicable; main-executable names
are omitted. Current metadata supplies source files but no instruction-to-source-line
mapping, so function offsets remain. Exact source lines require verified address-to-line
debug information for the crashed executable; guessing from decomp source would be inaccurate.

The included registry maps 15 GameCube/Wii configurations. Screenshot parsing and automatic
detection are verified against the seven supplied screenshots for **GM8E01_00, GM8E01_02,
and G2ME01**. Other configurations have maps but need screenshot validation. See
[evidence and coverage](docs/EVIDENCE.md) for provenance and REL limitations.

## Local use

Install Python 3.12+, [uv](https://docs.astral.sh/uv/), and native Tesseract with English
language data. Tesseract must be on `PATH`; Docker includes it automatically.

```sh
uv sync --locked
uv run crash-decoder decode tests/corpus/prime-allocation.png
uv run crash-decoder decode tests/corpus/*.png --json
uv run crash-decoder decode crash.txt --text --build GM8E01_00
uv run crash-decoder symbols validate
```

Pass image paths individually when your shell does not expand wildcards. Multiple files
are processed independently; a failed image produces its own error and exit status 1.
`--json` includes original OCR, selected text, all OCR passes, corrections, raw addresses,
detection evidence, and the symbol bundle checksum.

IP and saved return addresses remain separate. No return-address subtraction is applied.
Exact function starts resolve with offset zero; function ends, gaps, data addresses, and
unknown module layouts stay unresolved. Version numbers and address match counts never
select a build by themselves. A full build string can identify a registry entry, but cannot
establish that a modified executable matches its map.

## Run the Discord bot

1. Create an application and bot in the Discord Developer Portal.
2. Install it in a test server with `bot` and `applications.commands` scopes. Grant
   View Channel, Read Message History, Send Messages, Send Messages in Threads, and
   Attach Files. Message Content Intent is unnecessary for this context command.
3. Copy `.env.example` to `.env`; set the runtime token and optionally a test server ID.
4. Run `docker compose up -d --build` and check `docker compose logs -f decoder`.
5. Invoke **Decode crash** on a message in a channel, thread, or forum post. The selected
   message's attachments and image embeds are processed, including a forum starter message.

`DISCORD_GUILD_ID` syncs the command immediately into that test server. Without it, the bot
registers globally; Discord may take time to propagate the command. Clear a stale test-guild
command in the portal when switching installation modes to avoid duplicate entries.

The normal container exposes no ports, runs as UID 10001 with a read-only filesystem and
temporary `/tmp`, and uses a Gateway connection. Its health check requires a recent READY
heartbeat and live workers. Inspect status with `docker compose ps`. Docker's restart policy
restarts an exited process; an unhealthy status alone does not restart it.

Limits: 10 MB/image, 16 million input pixels, 30-second total download deadline, 40-second OCR
budget, 20 unique images/request, two OCR workers, eight queued jobs, and a 20-second per-user
cooldown. Duplicate active/recent requests are suppressed. Set `OCR_WORKERS` and
`QUEUE_CAPACITY` at runtime. Requests waiting longer than ten minutes receive a private
timeout. Images are deduplicated by URL and downloaded content, and failures stay isolated.

Only HTTPS image hosts operated by Discord are fetched. External embeds must provide a
Discord proxy URL; arbitrary destinations and redirects are rejected. Screenshots are kept
in memory and Tesseract's temporary files are cleaned up. Logs contain timings, failure
types, build identities and bundle versions rather than image contents or signed URLs.

For a local non-Docker bot, set `DISCORD_TOKEN` (and optionally `DISCORD_GUILD_ID`) in the
process environment and run `uv run crash-decoder bot`. `.env` is consumed by Compose; the
Python CLI does not automatically load it.

To enable diagnostic attachments in Docker, set `ATTACH_DIAGNOSTICS=true` in `.env` and run
`docker compose up -d --build`. Set it back to `false` and repeat the command for production.
The local decoder's `--json` option remains available independently of this setting.

## Update symbols and register custom builds

```sh
uv run crash-decoder symbols update --game all
uv run crash-decoder symbols update --game prime --prime-revision FULL_COMMIT_SHA
uv run crash-decoder symbols update --game all --prime-checkout /path/to/prime --echoes-checkout /path/to/echoes
uv run crash-decoder symbols diff previous-symbols.json src/crash_decoder/data/symbols.json
```

The updater fetches upstream revisions into `.cache/upstream`, records full SHAs, imports
functions and source splits without game assets, validates the bundle and writes a checksum
lock file. The same committed metadata and registry produce identical bytes. Local checkout
metadata must be committed. Revision options apply to fetched checkouts; local checkouts use
their current committed HEAD. One-game updates preserve the other game's maps and custom maps.

To add a separate map from a committed custom decomp checkout:

```sh
uv run crash-decoder symbols register --id prime-my-mod --game prime --checkout /path/to/mod --config config/GM8E01_00/config.yml --platform GameCube --region US --revision my-mod-1 --output /path/to/custom-symbols.json
```

Start by copying the default bundle and its lock to the chosen output path. The config's
symbol sizes, sections, splits and binary hashes must describe the custom executable.
Build IDs cannot replace existing identities. `--build-string` is optional and repeatable;
sharing a stock build string makes detection ambiguous and requires a private selection.
Verified linker-map/ELF import is a later extension; the current importer consumes decomp
metadata. Mount the custom bundle read-only and set `SYMBOL_BUNDLE` to its container path.

REL sections can be supplied to local decoding with `--rel-layout layout.json`, or through
the **Correct OCR** modal's optional JSON field:

```json
{"sections":{"AIMannedTurret":{".text":"0x81000000"}},"allocations":{"AIMannedTurret":"0x80ffff00"}}
```

This is an illustrative layout, not a measured game address. Use the actual layout from
the crashed process. Absolute module addresses require section bases. `RFO` allocation
offsets additionally require the module allocation base (or verified section file offsets
in a custom bundle). Layout identity and address bounds must yield one function; ambiguity
stays unresolved. Never apply another session's relocation layout.

## Verification and delivery

```sh
uv run ruff check .
uv run ruff format --check .
uv run pytest -q
docker build --target test -t discord-crash-decoder:test .
docker run --rm discord-crash-decoder:test
docker build -t discord-crash-decoder:local .
uv run python scripts/verify_update_cycle.py
```

Native OCR tests skip on hosts without Tesseract and run in the test container/CI. The tests
cover all seven real screenshots, uncertain detection, resolver boundaries, REL relocation,
safe downloads, queue limits, correction ownership, actual discord.py reply serialization
for channels/threads, and a deterministic symbol update/rollback cycle. No Discord token is
needed for these local checks. The cycle script uses committed synthetic metadata in an
isolated build context, builds baseline/updated images, and verifies lookup changes and
restoration by running the previous image again. It leaves the two tagged images for
inspection and writes `.cache/cycle-verification.json`. Live Discord and GHCR deployment
are left to the operator.

CI runs on pushes, pull requests and manual dispatch, validating lint, tests, maps and the
production container. New runs cancel older CI runs for the same ref. **Update symbols** accepts
optional upstream revisions and opens a PR with build additions, symbol changes and coverage
regressions. Enable GitHub Actions' permission to create PRs in repository settings. Merging
to `main` publishes the updated container; release tags `v*` publish version tags alongside
full `sha-COMMIT` tags. Existing commit images are reused to avoid overwriting immutable
commit tags. If your default branch differs, adjust the Publish workflow's branch filter.

For deployment, set `DECODER_IMAGE=ghcr.io/OWNER/REPO:sha-FULL_COMMIT_SHA` in `.env`, then run:

```sh
docker compose pull decoder
docker compose up -d --no-build decoder
```

Record the image digest and bundle checksum before upgrades. Roll back by restoring the
previous image reference in `.env` and repeating those commands; code and maps revert
together. A development `SYMBOL_BUNDLE` mount takes precedence over the bundled maps, so
remove or roll back that override as well. [Architecture](docs/ARCHITECTURE.md) describes the
module boundaries and extension points.
