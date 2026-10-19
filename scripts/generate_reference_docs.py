"""Build the standalone reference and portable exports from one strict catalogue."""

from __future__ import annotations

import argparse
from pathlib import Path

from valiance.elements.reference_docs import (
    collect_language_references,
    render_language_reference,
)


def main() -> int:
    """Generate an offline HTML entry point, Markdown, and JSON in one directory."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("build/reference"))
    args = parser.parse_args()
    references = collect_language_references()
    documents = {
        filename: render_language_reference(references, output_format=output_format)
        for filename, output_format in (
            ("index.html", "html"),
            ("language-reference.md", "markdown"),
            ("language-reference.json", "json"),
        )
    }
    args.output.mkdir(parents=True, exist_ok=True)
    for filename, content in documents.items():
        (args.output / filename).write_text(content, encoding="utf-8", newline="\n")
    print(f"Generated {len(references)} entries in {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
