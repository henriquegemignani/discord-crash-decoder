# Evidence and compatibility

The initial corpus consists of seven real screenshots supplied by the user on 2026-10-05.
Original images are retained in `tests/corpus`; `expected.json` contains manually checked IP
and saved-return-address labels. OCR tests compare every extracted address against those
labels, not merely the number of recognized frames. The invalid return `0xffffeab3`, null
IP, and Echoes return `0x806bf338` remain unresolved unless an appropriate module layout exists.
`expected-traces.txt` records the decoded address labels against the pinned metadata. The
expected IP function identities and offsets were checked against the upstream symbols and
are asserted in both text and native OCR tests.

| Screenshot | Registry entry | Case |
| --- | --- | --- |
| prime-allocation.png | GM8E01_00 | Wrapped allocation failure, eight stack rows |
| prime-exception.png | GM8E01_00 | Exception 2, nine stack rows |
| prime-loop.png | GM8E01_00 | Infinite loop, invalid saved return, nine stack rows |
| prime-revision2.png | GM8E01_02 | Exception 3, null IP, nine stack rows |
| echoes-allocation.png | G2ME01 | Allocation failure, eight stack rows |
| echoes-exception.png | G2ME01 | Exception 2, possible dynamic-module return, nine rows |
| echoes-object-list.png | G2ME01 | Object list full, emulator chrome, nine stack rows |

These screenshots establish parsing and full-header detection for those registry entries.
They do not prove that modified executables are identical to stock binaries, nor do they
verify every function name against a debugger trace. Function names and sizes come from the
pinned upstream metadata; independent boundary tests reject exact ends, gaps and data regions.
`scripts/smoke.py` verifies the same corpus using the non-root production image.

Upstream provenance is stored in `src/crash_decoder/data/symbols.lock.json` and each build.
The first imported revisions were Prime `e46cbd6f9d94b2c1d4989856f16dfc2d2dda049e` and
Echoes `56510a4ee5bd7e2075363af213c610c4165f733c`. The updater also fetched those revisions
independently and reproduced the same metadata bundle. The projects publish metadata under
CC0; a copy is included in the package's data/licenses directory.

Full build strings are curated in `registry.json` and augmented by upstream build-string
files. Prime's source explicitly branches among several release headers. Echoes' dummy header
is shared source scaffolding, so it is **not** copied to every region automatically: G2ME01's
header is supported by the supplied screenshots. Missing regional strings remain unknown.
The version `v1.111` alone must not imply a US revision because Japan uses that version too.

Relevant upstream evidence:

- [Prime build string](https://github.com/PrimeDecomp/prime/blob/main/config/GM8E01_00/build_string.txt)
- [Prime header selection](https://github.com/PrimeDecomp/prime/blob/main/src/MetroidPrime/CFrontEndUI.cpp)
- [Prime crash handler](https://github.com/PrimeDecomp/prime/blob/main/src/Kyoto/Basics/RAssertDolphin.cpp)
- [Echoes crash handler](https://github.com/PrimeDecomp/echoes/blob/main/src/Kyoto/Basics/RAssertDolphin.cpp)
- [Echoes REL registration](https://github.com/PrimeDecomp/echoes/blob/main/src/MetroidPrime/CRelFile.cpp)
- [Echoes module configuration](https://github.com/PrimeDecomp/echoes/blob/main/config/G2ME01/config.yml)

Both handlers print `IP` from SRR0 and stack rows as stack pointer, back chain, saved LR.
Echoes additionally emits `RFO:0xOFFSET: MODULE` for some saved returns, using the saved LR
minus `CRelFileDebugInfo::GetStart()`. `CRelFile::Link()` registers `mData.get()` as that start:
the REL allocation base, not a `.text` section base. Upstream symbol offsets are section-relative.
No supplied image contains an RFO row or runtime relocation table, so RFO syntax/translation
is source-backed and tested with explicit synthetic layouts rather than claimed screenshot
verification. Unknown REL layouts stay unresolved. US Echoes supplies module maps; Japanese
and PAL configs contain module identities/hashes without corresponding symbol/split metadata.
Those omissions remain explicit limitations in the bundle. Wii RSO coverage is limited to
main executables; RSO module import is not implemented.

Three flags are tracked independently for every build: map availability, screenshot parsing,
and automatic detection. All 15 imported configs have main-executable maps; only the three
corpus builds have the latter two flags. Adding a config file alone does not grant screenshot
support. To expand validation, add original images, label all addresses, add expected traces,
run native OCR tests, and update the registry flags after they pass.

Discord routing is checked locally through actual discord.py Message/TextChannel/Thread
reply serialization with a mocked HTTP boundary. The tests assert the message reference,
mentions suppression, private acknowledgement, reply link, per-image errors and correction
ownership. Live channel/thread/forum interaction checks and GHCR publication are deliberately
left for the user's later deployment; no bot credential or remote repository was configured.
