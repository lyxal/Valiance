"""Validated terminal palettes shared by CSS, source tokens and transcript events."""

from __future__ import annotations

import os
import re

from textual.theme import Theme

REFERENCE = {
    "background": "#0d0d0d",
    "text": "#eeeeee",
    "panel": "#141414",
    "gutter": "#191919",
    "gutter_text": "#aaaaaa",
    "tabs": "#3e3e3e",
    "active_tab": "#595959",
    "divider": "#777777",
    "focus": "#8bb9df",
    "selection": "#354761",
    "input": "#1c2330",
    "status": "#aaaaaa",
    "completion": "#202532",
    "keyword": "#c552c5",
    "type": "#36c5e8",
    "name": "#eeeeee",
    "number": "#efa976",
    "string": "#d5a1c8",
    "operator": "#eeeeee",
    "comment": "#999999",
    "diagnostic": "#ef8794",
    "warning": "#dcb778",
    "result": "#88e790",
    "output": "#eeeeee",
    "command": "#bfc9e0",
    "notice": "#aaaaaa",
}

THEMES = {
    "reference-dark": ("Reference dark", {}),
    "high-contrast": (
        "High contrast",
        {
            "background": "#000000",
            "text": "#ffffff",
            "panel": "#000000",
            "name": "#ffffff",
            "divider": "#cccccc",
            "status": "#ffffff",
            "comment": "#cccccc",
            "keyword": "#ff88ff",
            "diagnostic": "#ff8888",
        },
    ),
    "ocean": (
        "Ocean",
        {
            "background": "#101c28",
            "panel": "#162b3b",
            "tabs": "#203b4e",
            "active_tab": "#315770",
        },
    ),
    "plum": (
        "Plum",
        {
            "background": "#211627",
            "panel": "#2d2035",
            "tabs": "#402d4d",
            "active_tab": "#60446e",
        },
    ),
    "amber": (
        "Amber",
        {
            "background": "#211b12",
            "panel": "#302719",
            "text": "#f3dfb0",
            "name": "#f3dfb0",
            "tabs": "#443824",
            "active_tab": "#655334",
        },
    ),
    "paper": (
        "Paper",
        {
            "background": "#faf8f0",
            "text": "#262626",
            "panel": "#eeeade",
            "gutter": "#e5e0d5",
            "gutter_text": "#555555",
            "tabs": "#ded8cc",
            "active_tab": "#c3bbab",
            "divider": "#626262",
            "focus": "#174e8c",
            "selection": "#c1d5eb",
            "input": "#e5edf5",
            "status": "#555555",
            "completion": "#eeeade",
            "keyword": "#8b167b",
            "type": "#00657d",
            "name": "#262626",
            "number": "#8f400c",
            "string": "#805080",
            "operator": "#262626",
            "comment": "#626262",
            "diagnostic": "#b51d38",
            "warning": "#805300",
            "result": "#14652d",
            "output": "#262626",
            "command": "#294768",
            "notice": "#555555",
        },
    ),
}

ANSI = dict.fromkeys(REFERENCE, "ansi_default") | {
    "keyword": "ansi_bright_magenta",
    "type": "ansi_bright_cyan",
    "number": "ansi_yellow",
    "string": "ansi_magenta",
    "comment": "ansi_cyan",
    "diagnostic": "ansi_bright_red",
    "warning": "ansi_yellow",
    "result": "ansi_bright_green",
    "focus": "ansi_bright_blue",
    "selection": "ansi_blue",
    "active_tab": "ansi_blue",
    "divider": "ansi_cyan",
    "command": "ansi_bright_white",
}
COLOR_MODES = ("auto", "truecolor", "ansi", "monochrome")


def valid_color(value: object) -> bool:
    """Accept only opaque RGB values or the terminal's sixteen ANSI colors."""
    return isinstance(value, str) and bool(
        re.fullmatch(r"#[0-9a-fA-F]{6}", value)
        or re.fullmatch(
            r"ansi_(?:default|(?:bright_)?(?:black|red|green|yellow|blue|magenta|cyan|white))",
            value,
        )
    )


def color_mode(requested: str, system: str | None) -> str:
    """Respect explicit preferences, NO_COLOR, and the detected terminal depth."""
    if "NO_COLOR" in os.environ:
        return "monochrome"
    if requested != "auto":
        return requested
    if os.environ.get("TERM") == "dumb" or system is None:
        return "monochrome"
    return "ansi" if system in {"standard", "windows"} else "truecolor"


def palette(theme: str, roles: dict[str, str], mode: str) -> dict[str, str]:
    """Resolve a named palette and safe overrides, with deterministic fallbacks."""
    if mode == "monochrome":
        return dict.fromkeys(REFERENCE, "ansi_default")
    if mode == "ansi":
        return ANSI | {
            key: value
            for key, value in roles.items()
            if key in REFERENCE and valid_color(value) and value.startswith("ansi_")
        }
    return (
        REFERENCE
        | THEMES.get(theme, THEMES["reference-dark"])[1]
        | {
            key: value
            for key, value in roles.items()
            if key in REFERENCE and valid_color(value)
        }
    )


def rich_color(color: str) -> str:
    """Translate CSS terminal color names into Rich's ANSI spelling."""
    return color.removeprefix("ansi_") if color.startswith("ansi_") else color


def application_theme(
    name: str, colors: dict[str, str], mode: str, *, dark: bool = True
) -> Theme:
    """Supply matching defaults to framework widgets including modal controls."""
    return Theme(
        name=name,
        primary=colors["focus"],
        foreground=colors["text"],
        background=colors["background"],
        surface=colors["background"],
        panel=colors["panel"],
        error=colors["diagnostic"],
        warning=colors["warning"],
        success=colors["result"],
        dark=dark if mode == "truecolor" else False,
    )
