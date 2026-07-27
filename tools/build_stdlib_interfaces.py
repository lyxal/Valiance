"""Prebuild analysed interfaces and runtime programs for installed stdlib modules."""
from __future__ import annotations

import argparse
from pathlib import Path

from valiance.main import _compile_module_artifact


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path)
    parser.add_argument("--no-optimize", action="store_true")
    args = parser.parse_args(argv)
    root = args.root or Path(__file__).resolve().parents[1] / "src" / "valiance" / "std"
    sources = sorted(root.rglob("*.vlnc"))
    for source in sources:
        relative = source.relative_to(root).with_suffix("")
        module_name = "std." + ".".join(relative.parts)
        try:
            _compile_module_artifact(
                source,
                source.with_suffix(".vbcm"),
                module_name=module_name,
                optimize=not args.no_optimize,
            )
        except Exception as exc:
            print(f"skipped {module_name}: {exc}")
            continue
        print(f"built {module_name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
