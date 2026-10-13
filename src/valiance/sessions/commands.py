"""Shared session queries and branch commands, independent of terminal widgets."""

from dataclasses import dataclass

from .service import SessionService


def parse_branch_command(source: str) -> tuple[str, int | None, str | None] | None:
    """Validate the existing branch-command namespace and positive counts."""
    parts = source.split()
    if not parts or parts[0] not in {
        ":branch",
        ":restore",
        ":continue",
        ":copy",
        ":escape",
    }:
        return None
    name = parts[0][1:]
    if name in {"branch", "restore", "continue"}:
        return name, None, f"usage: :{name}" if len(parts) != 1 else None
    if len(parts) > 2:
        return name, None, f"usage: :{name} [positive count]"
    if len(parts) == 1:
        return name, 1, None
    if not parts[1].isdigit() or parts[1].startswith("0"):
        return name, None, f"usage: :{name} [positive count]"
    return name, int(parts[1]), None


@dataclass(frozen=True)
class CommandReply:
    """Presentation text for a non-executing query or session branch operation."""

    text: str
    successful: bool = True


def session_command(session: SessionService, source: str) -> CommandReply | None:
    """Handle session queries and branches without parsing terminal commands as code."""
    source = source.strip()
    if source in {":help", ":h", "help"}:
        return CommandReply(
            ":type <source> — preview stack types without execution\n"
            ":branch / :restore / :continue — open, discard or adopt a runtime branch\n"
            ":copy [n] / :escape [n] — duplicate or move values into its parent\n"
            ":clear — clear transcript; :reset — reset session and transcript\n"
            ":repl / :scratch — focus REPL input or source editor\n"
            ":quit — close the editor through its document protection prompts"
        )
    if source == ":type" or source.startswith(":type "):
        expression = source.removeprefix(":type").strip()
        return CommandReply(
            session.type_hint(expression) or "No type information available."
            if expression
            else "usage: :type <Valiance source>",
            bool(expression),
        )
    parsed = parse_branch_command(source)
    if parsed is None:
        return None
    name, count, error = parsed
    if error:
        return CommandReply(error, False)
    try:
        if name == "branch":
            session.open_branch()
            text = f"Opened REPL branch at depth {session.branch_depth}."
        elif name == "restore":
            session.restore_branch()
            text = f"Restored parent REPL frame; depth is {session.branch_depth}."
        elif name == "continue":
            session.continue_branch()
            text = f"Continued branch into parent; depth is {session.branch_depth}."
        elif name == "copy":
            session.copy_to_parent(count)
            text = f"Copied {count} value(s) to the parent frame."
        else:
            session.escape_to_parent(count)
            text = f"Escaped {count} value(s) to the parent frame."
    except ValueError as exc:
        return CommandReply(f"REPL branch error: {exc}", False)
    return CommandReply(text)
