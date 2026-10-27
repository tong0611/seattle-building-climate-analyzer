"""Command-line interface.

    beps run [--download]     every step, raw CSV to app tables
    beps <step>               one step, e.g. `beps quality` or `beps ingest --download`
"""

from __future__ import annotations

import argparse

from beps_prioritizer.pipeline import STEPS, run_all


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="beps", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="run every step in order")
    run.add_argument("--download", action="store_true", help="re-download the raw CSV")
    for name, step in STEPS.items():
        p = sub.add_parser(name, help=(step.__doc__ or "").strip().splitlines()[0])
        if name == "ingest":
            p.add_argument("--download", action="store_true", help="re-download the raw CSV")
    args = parser.parse_args(argv)

    if args.command == "run":
        run_all(download=args.download)
    elif args.command == "ingest":
        print(STEPS["ingest"](download=args.download))
    else:
        print(STEPS[args.command]())


if __name__ == "__main__":
    main()
