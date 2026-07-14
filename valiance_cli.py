"""Build-safe launcher for the Valiance command-line interface.

Keep the standalone executable entry point outside ``src/valiance``.  When a
freezer executes ``src/valiance/main.py`` as a script, that directory can be
placed at the front of ``sys.path``.  Its ``types`` package can then shadow
Python's standard-library ``types`` module during interpreter start-up,
causing a partially-initialized-module error before Valiance itself is loaded.
"""

from valiance.main import main


if __name__ == "__main__":
    raise SystemExit(main())
