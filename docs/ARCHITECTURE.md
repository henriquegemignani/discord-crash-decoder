# Architecture

`src/crash_decoder` contains a Discord-independent decoder and a thin interaction adapter.

| Module | Responsibility |
| --- | --- |
| `models.py` | Typed crash, frame, detection and resolution records |
| `images.py` | Restricted streaming downloads, image validation, bounded native Tesseract passes |
| `parser.py` | Full build strings, exception text, IP, registers, stack chains and Echoes RFO rows |
| `detection.py` | Evidence-driven build candidates and explicit uncertainty |
| `resolver.py` | Executable-section/function bounds, CodeWarrior demangling and REL relocation |
| `bundle.py` | Schema checks, deterministic serialization and checksums |
| `symbol_builder.py` | Committed decomp metadata imports, locks, custom registration and change reports |
| `layout.py` | Validation of explicitly supplied runtime REL layout |
| `report.py` | Text report, Discord summary and diagnostic JSON |
| `jobs.py` | Bounded queue, workers, cooldowns, request/content deduplication and isolated failures |
| `bot.py` | Gateway client, message context command, replies, private controls and health heartbeat |
| `cli.py` | Local decoding, bot startup and maintainer commands |

The adapter defers privately before doing work, submits a bounded job, and processes every
accepted screenshot independently. Downloading uses async HTTP; Pillow and OCR execute in
worker threads. One actual Discord message reply carries all results with mentions disabled.
Long reports become text attachments. `ATTACH_DIAGNOSTICS=true` enables diagnostic JSON on
both replies and corrections; production defaults to no JSON attachment. Controls
remain on the private interaction message for ten minutes; only the original requester can
edit. A correction snapshots its screenshot index so changing the dropdown while a modal is
open cannot edit another image. Updates edit the same public reply without rerunning OCR.

The resolver does not infer relocation or subtract four bytes from saved return addresses.
Main executables use absolute addresses; REL functions use section offsets. Explicit runtime
section bases identify loaded modules. Echoes `RFO` values are allocation-relative and need
an allocation-to-section translation before lookup. Conflicting ranges are unresolved.

Bundles store each build's game/platform/region/revision/executable identity, upstream commit,
known full strings, verification flags, binary hashes, code sections, functions, sizes,
original names, source associations and limitations. Runtime validation rejects corruption,
overlaps and invalid bounds. Checksums cover canonical content excluding the checksum itself.
The lock adds upstream SHAs and checksum consistency; custom provenance is tracked separately.

Production images include validated code and maps, locked production dependencies, native
Tesseract and English data. CI uses a separate test stage so pytest, Ruff and Git are excluded
from production. Secrets enter through runtime environment variables. A mounted bundle
override supports development without changing the container; it must pass the same checks.
