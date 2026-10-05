# Discord Crash Decoder Project Plan

Build a Python bot that replaces [prime-crash-parser](https://github.com/MetroidPrimeModding/prime-crash-parser). A user right-clicks a Discord message, selects **Apps → Decode crash**, and the bot reads every image on that message, decodes any Prime or Echoes crash screens, and posts one reply to the original message. Use `uv` for Python dependencies, package the bot in Docker, and build and publish the image with GitHub Actions.

The existing tool uses Tesseract OCR, extracts the instruction pointer and stack addresses, and resolves them against one of two manually selected symbol maps. Preserve that workflow while improving automatic build detection, symbol accuracy, and support for multiple images. [Existing implementation](https://github.com/MetroidPrimeModding/prime-crash-parser/blob/gh-pages/crash.html)

## Discord interaction and result

Register a message context command named **Decode crash**. When invoked, the bot should immediately acknowledge the request privately, collect all image attachments and image embeds on the selected message, remove duplicate images, and process each image independently. Post one public reply to the original message, with a section per screenshot and a full text attachment if the result is too long. Complete the private acknowledgement with a link to the reply.

Use an actual message reply with mentions disabled. A Discord interaction follow-up alone does not establish a reply to the selected message. Initially, “post” means the selected message, including a forum post's starter message; the bot does not recursively scan an entire thread. Report unreadable or unrelated images individually without discarding successful results.

Discord requires an initial interaction response within three seconds, so defer before OCR. The selected message is an explicit exception to the privileged Message Content Intent requirement, allowing this interaction without reading every message in a server. [Interaction timing](https://docs.discord.com/developers/interactions/receiving-and-responding), [message-content access](https://docs.discord.com/developers/events/gateway#message-content-intent)

## Application structure

Keep decoding independent of Discord so it can run locally and in tests.

| Component | Responsibility |
| --- | --- |
| Discord adapter | Context command, permissions, progress, replies, correction controls |
| Image reader | Downloads, format validation, preprocessing, OCR |
| Crash parser | Build text, exception type, instruction pointer, registers, stack frames |
| Binary detector | Selects a supported game and build from available evidence |
| Symbol resolver | Converts addresses into function names and offsets |
| Symbol builder | Imports decomp metadata and produces versioned bundles |
| Report renderer | Discord summary, full text, diagnostic JSON |

Use `discord.py`, Pillow, native Tesseract through `pytesseract`, an asynchronous HTTP client, typed data models, `pytest`, and Ruff. Lock Python dependencies with uv. Run one container with a bounded job queue and limited OCR concurrency. Keep OCR and image processing off Discord's event loop. A Gateway connection avoids a public HTTP endpoint.

## OCR and stack decoding

Start with native Tesseract, following the existing tool's approach. Evaluate it against real screenshots before adding another OCR engine. Try a small set of preprocessing passes: scaling, grayscale, contrast adjustment, and thresholding. Read the header with ordinary text recognition, then use hexadecimal constraints for address regions.

Parse into structured records while preserving original OCR text and any corrections. Correct common confusions such as `O` versus `0` only inside address fields; never silently invent missing hexadecimal digits.

The symbol resolver should resolve the instruction pointer separately from saved return addresses; return function name, offset, binary or module, and source file where available; handle exact function starts; and respect function sizes and executable sections. Leave gaps and invalid addresses unresolved. Preserve raw return addresses, and make any call-site adjustment only after verifying the crash-screen format. Demangle CodeWarrior names while retaining the original symbol.

This addresses a weakness in the existing lookup: it finds a preceding symbol without checking whether the address lies inside that function, and its strict comparison mishandles exact symbol starts. [Current lookup code](https://github.com/MetroidPrimeModding/prime-crash-parser/blob/gh-pages/crash.html)

## Automatic game and binary detection

Maintain a registry keyed by **game, platform, region, revision, and executable or module**, rather than only Prime versus Echoes. Evaluate evidence in this order:

| Evidence | Use |
| --- | --- |
| Full build string, including date and time | Strong identification when uniquely mapped |
| Explicit game, region, or revision text | Narrows candidates |
| Known crash-screen format | Supporting evidence |
| Address patterns and known anchors | Additional consistency checks |
| User selection | Resolves ambiguous or unsupported cases |

Prime includes build-string metadata, including `Build v1.088 10/29/2002 2:21:25`. Version numbers alone are insufficient: its documented Japanese and US releases both include v1.111. [Build-string file](https://github.com/PrimeDecomp/prime/blob/main/config/GM8E01_00/build_string.txt), [Prime version list](https://github.com/PrimeDecomp/prime)

Do not select a binary merely because it resolves the most addresses. Different builds can have overlapping address ranges, so an incorrect map may still return plausible names. Mark each detection as **identified** when evidence is unique and reliable, **tentative** when evidence is incomplete, or **unknown** when evidence is insufficient or conflicting.

For ambiguity, offer a private game/build selector and rerun symbol resolution using the existing OCR result. Also offer text correction for poor OCR. A screenshot can identify a known build, but it cannot prove that a modded executable is byte-for-byte identical. Register custom binaries with separate maps.

Echoes needs special handling for dynamically loaded REL modules. Its decomp has separate module symbol tables. Resolve a module address only when module identity and the necessary runtime section addresses or offsets are available; otherwise show it unresolved. Establish what real crash screens expose during the first milestone. [Echoes module configuration](https://github.com/PrimeDecomp/echoes/blob/main/config/G2ME01/config.yml)

## Symbol-map updates

Generate symbol bundles directly from decomp metadata for routine updates. Both projects publish symbol addresses and sizes; their configuration identifies binaries, and split files associate address ranges with source files. This supports a metadata-based generator without requiring a full game build. [Prime symbols](https://github.com/PrimeDecomp/prime/blob/main/config/GM8E01_00/symbols.txt), [Echoes configuration](https://github.com/PrimeDecomp/echoes/blob/main/config/G2ME01/config.yml)

The maintainer command should be:

```text
uv run crash-decoder symbols update --game all
```

It should fetch both repositories, resolve their revisions to commit SHAs, import supported configurations, validate the result, and write a deterministic bundle plus a lock file. Each bundle should contain game/build identities and known build strings; function addresses, sizes, names, sections, and source associations; module metadata and coverage limitations; upstream commits; binary hashes where supplied; a schema version; and a bundle checksum.

Add a GitHub Actions **Update symbols** workflow with optional upstream revision inputs. It runs the same command, validates the bundle, and opens a PR showing new builds, changed symbols, and coverage regressions. Merging that PR publishes an updated container through the normal release pipeline. Also accept a local decomp checkout for contributors. Later, add import of a verified linker map or ELF for custom builds that require compilation.

Bundle validated symbols into the image by default so an image update changes code and maps together and rollback restores both. A mounted bundle override can support local development.

## Docker and GitHub Actions

Use a multi-stage Dockerfile with a pinned Python base and uv version. Install locked production dependencies, Tesseract and its language data, application code, and the validated symbol bundle. Run as a non-root user. [uv Docker guidance](https://docs.astral.sh/uv/guides/integration/docker/)

| Workflow | Trigger | Outcome |
| --- | --- | --- |
| CI | Pull requests and pushes | Lint, tests, symbol validation, container smoke test |
| Publish | Release tags | Build and push versioned images to GHCR |
| Update symbols | Manual dispatch | Rebuild bundles and open an update PR |

Publish immutable commit tags alongside release tags using [GitHub's container-publishing workflow](https://docs.github.com/en/actions/tutorials/publish-packages/publish-docker-images). Supply the Discord token only at runtime. Include Docker Compose configuration, restart policy, health checks, and deployment instructions.

Add download and pixel limits, OCR timeouts, bounded queues, per-user cooldowns, and duplicate-request suppression. Download Discord-hosted or proxied images where possible; restrict destinations and redirects for any external-image fetching. Log timings, failures, detected builds, and bundle versions. Keep screenshots temporary. If the bot cannot reply because of permissions or a deleted message, deliver a clear private error.

## Milestones and acceptance criteria

| Milestone | Deliverable | Acceptance |
| --- | --- | --- |
| 1. Evidence and compatibility | Labeled screenshots, expected traces, binary registry | Verified examples from both games; documented build and REL evidence |
| 2. Decoder and symbol builder | Local image/text decoding and decomp imports | Correct boundary lookups; reproducible bundles; unknown addresses preserved |
| 3. Discord MVP | Context command, multiple-image processing, public reply | Works in channels and threads; one bad image does not fail the message |
| 4. Detection and corrections | Build detection, uncertainty, selectors, OCR editing | Cropped or ambiguous screenshots do not receive falsely certain identification |
| 5. Deployment | Docker, GHCR publishing, symbol-update workflow | Clean deployment and a complete tested symbol-update/rollback cycle |

Begin validation with Prime `GM8E01_00` and Echoes `G2ME01`, then expand across available regional and Wii configurations. Track **map availability**, **verified screenshot parsing**, and **verified automatic detection** separately: a symbol file alone does not establish full support for a binary.

The first implementation priority is the screenshot corpus and binary registry. Those establish whether automatic detection is trustworthy before the Discord integration makes its output public.
