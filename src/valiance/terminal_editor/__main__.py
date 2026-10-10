"""Internal editor evaluation route; public CLI defaults remain unchanged."""

import os
from pathlib import Path

from .app import EditorApp


def main() -> None:
    """Launch a blank editor and persist only bounded recent paths/preferences."""
    base = Path(
        os.environ.get("LOCALAPPDATA")
        or os.environ.get("XDG_CONFIG_HOME")
        or Path.home() / ".config"
    )
    EditorApp(config_path=base / "valiance" / "terminal-editor.json").run()


if __name__ == "__main__":
    main()
