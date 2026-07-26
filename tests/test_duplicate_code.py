"""Guard against copy-pasted production function implementations."""

import ast
import hashlib
import unittest
from pathlib import Path


class DuplicateCodeTests(unittest.TestCase):
    def test_production_functions_do_not_duplicate_substantial_bodies(self):
        """Reject novec duplicate function bodies large enough to require one owner."""
        source_root = Path(__file__).parents[1] / "src" / "valiance"
        implementations: dict[str, list[str]] = {}
        for path in source_root.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                body = ast.dump(
                    ast.Module(body=node.body, type_ignores=[]),
                    include_attributes=False,
                )
                if len(body) < 350:
                    continue
                digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
                relative = path.relative_to(source_root.parent)
                implementations.setdefault(digest, []).append(
                    f"{relative}:{node.lineno}: {node.name}"
                )

        duplicates = [locations for locations in implementations.values() if len(locations) > 1]
        self.assertEqual(
            duplicates,
            [],
            "Substantial duplicate production function bodies found:\n"
            + "\n\n".join("\n".join(group) for group in duplicates),
        )
