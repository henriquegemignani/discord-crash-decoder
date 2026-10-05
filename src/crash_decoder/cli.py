import argparse
import json
import logging
import subprocess
import time
from pathlib import Path

from .bundle import DEFAULT_BUNDLE, load
from .decoder import Decoder
from .report import diagnostic, render


def main():
    parser = argparse.ArgumentParser(description="Metroid Prime/Echoes crash decoder")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("bot", help="Start Discord Gateway bot")
    commands.add_parser("health", help="Check Gateway heartbeat")
    decode = commands.add_parser("decode", help="Decode local images or OCR text")
    decode.add_argument("files", nargs="+", type=Path)
    decode.add_argument("--text", action="store_true")
    decode.add_argument("--build")
    decode.add_argument("--bundle", type=Path, default=DEFAULT_BUNDLE)
    decode.add_argument("--json", action="store_true")
    decode.add_argument("--rel-layout", type=Path, help="Runtime REL sections/allocation JSON")
    symbols = commands.add_parser("symbols")
    actions = symbols.add_subparsers(dest="action", required=True)
    update = actions.add_parser("update")
    update.add_argument("--game", choices=["all", "prime", "echoes"], default="all")
    update.add_argument("--prime-revision", default="main")
    update.add_argument("--echoes-revision", default="main")
    update.add_argument("--prime-checkout", type=Path)
    update.add_argument("--echoes-checkout", type=Path)
    update.add_argument("--registry", type=Path, default=Path("registry.json"))
    update.add_argument("--output", type=Path, default=DEFAULT_BUNDLE)
    update.add_argument("--cache", type=Path, default=Path(".cache/upstream"))
    validate = actions.add_parser("validate")
    validate.add_argument("file", nargs="?", type=Path, default=DEFAULT_BUNDLE)
    compare = actions.add_parser("diff")
    compare.add_argument("old", type=Path)
    compare.add_argument("new", type=Path)
    register = actions.add_parser(
        "register", help="Register custom binary from committed decomp metadata"
    )
    register.add_argument("--id", required=True)
    register.add_argument("--game", choices=["prime", "echoes"], required=True)
    register.add_argument("--checkout", type=Path, required=True)
    register.add_argument("--config", required=True, help="Config path relative to checkout")
    register.add_argument("--platform", choices=["GameCube", "Wii"], required=True)
    register.add_argument("--region", required=True)
    register.add_argument("--revision", required=True)
    register.add_argument("--build-string", action="append", default=[])
    register.add_argument("--output", type=Path, default=DEFAULT_BUNDLE)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        if args.command == "bot":
            from .bot import run

            run()
        elif args.command == "health":
            from .bot import HEALTH

            if not HEALTH.exists() or time.time() - float(HEALTH.read_text()) > 60:
                raise ValueError("Gateway heartbeat is stale")
        elif args.command == "decode":
            decoder = Decoder(load(args.bundle))
            from .layout import parse_layout

            sections, allocations = parse_layout(
                args.rel_layout.read_text() if args.rel_layout else ""
            )
            results = []
            for path in args.files:
                from .models import Result

                try:
                    if args.text:
                        result = decoder.text(
                            path.read_text(encoding="utf-8"),
                            path.name,
                            args.build,
                            runtime_sections=sections,
                            module_allocations=allocations,
                        )
                    else:
                        result = decoder.image(path.read_bytes(), path.name)
                        if (args.build or sections) and result.crash:
                            previous = result.crash
                            result = decoder.text(
                                previous.text,
                                path.name,
                                args.build,
                                previous.original_text,
                                sections,
                                allocations,
                            )
                            result.crash.ocr_passes = previous.ocr_passes
                    results.append(result)
                except (ValueError, OSError, RuntimeError) as exc:
                    results.append(
                        Result(
                            path.name, error=str(exc), bundle_checksum=decoder.bundle["checksum"]
                        )
                    )
            print(diagnostic(results) if args.json else render(results))
            if any(result.error for result in results):
                raise SystemExit(1)
        elif args.action == "validate":
            bundle = load(args.file)
            lock_file = args.file.with_suffix(".lock.json")
            if lock_file.exists():
                lock = json.loads(lock_file.read_text())
                if lock["bundle_checksum"] != bundle["checksum"] or lock["upstreams"] != {
                    b["game"]: b["upstream_commit"] for b in bundle["builds"] if not b.get("custom")
                }:
                    raise ValueError("Bundle lock does not match")
                if lock.get("custom_upstreams", {}) != {
                    b["id"]: b["upstream_commit"] for b in bundle["builds"] if b.get("custom")
                }:
                    raise ValueError("Custom bundle lock does not match")
            print(f"Valid: {len(bundle['builds'])} builds, SHA256 {bundle['checksum']}")
        elif args.action == "diff":
            from .symbol_builder import diff

            print(diff(load(args.old), load(args.new)), end="")
        elif args.action == "register":
            from .symbol_builder import register

            register(
                args.output,
                args.checkout,
                args.config,
                args.id,
                args.game,
                args.platform,
                args.region,
                args.revision,
                args.build_string,
            )
            print(f"Registered {args.id} in {args.output}")
        else:
            from .symbol_builder import update

            roots = {
                game: path
                for game in ("prime", "echoes")
                if (path := getattr(args, f"{game}_checkout"))
            }
            update(
                args.output,
                args.registry,
                ["prime", "echoes"] if args.game == "all" else [args.game],
                roots,
                {"prime": args.prime_revision, "echoes": args.echoes_revision},
                args.cache,
            )
            print(f"Wrote {args.output}")
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        parser.exit(1, f"Error: {exc}\n")


if __name__ == "__main__":
    main()
