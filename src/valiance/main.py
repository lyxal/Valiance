from __future__ import annotations

import argparse


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="valiance")
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser(
        "types",
        help="open the type-system explorer",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "types":
        from valiance.type_explorer import main as type_explorer_main

        type_explorer_main()
        return 0

    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
