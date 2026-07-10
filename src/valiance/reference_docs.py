"""Collect and render built-in and standard-library reference documentation."""

from __future__ import annotations

import html
import json
import re
from dataclasses import asdict, dataclass
from importlib import resources
from typing import Sequence

import valiance.types as T
from valiance.analysis.builtins import BUILTIN_ELEMENTS, BuiltinElement
from valiance.asts import DefineNode
from valiance.documentation import (
    DocumentationExample,
    ElementDocumentation,
    ParameterDocumentation,
)
from valiance.parsing import parse
from valiance.source_tools import DefinitionReference, extract_documented_defines
from valiance.stdlib_native import NativeFunction, native_stdlib_functions

REFERENCE_SCHEMA_VERSION = 1


class DocumentationError(ValueError):
    """Raised when a public built-in or stdlib function lacks valid metadata."""


@dataclass(frozen=True, slots=True)
class ElementReference:
    """Serializable documentation for one built-in or standard-library element."""

    name: str
    qualified_name: str
    scope: str
    module: str | None
    category: str
    summary: str
    description: tuple[str, ...]
    parameters: tuple[ParameterDocumentation, ...]
    returns: str | None
    examples: tuple[DocumentationExample, ...]
    notes: tuple[str, ...]
    see_also: tuple[str, ...]
    overloads: tuple[str, ...]
    aliases: tuple[str, ...] = ()
    source_path: str | None = None


def collect_builtin_references(*, strict: bool = True) -> tuple[ElementReference, ...]:
    """Collect one reference entry for each canonical globally available built-in."""
    aliases: dict[str, list[str]] = {}
    canonical_elements: list[BuiltinElement] = []
    for element in BUILTIN_ELEMENTS:
        if element.canonical_name is not None:
            aliases.setdefault(element.canonical_name.text, []).append(element.name.text)
            continue
        canonical_elements.append(element)

    references: list[ElementReference] = []
    missing: list[str] = []
    for element in canonical_elements:
        documentation = element.documentation
        if documentation is None:
            missing.append(element.name.text)
            continue
        references.append(
            ElementReference(
                name=element.name.text,
                qualified_name=element.name.text,
                scope="built-in",
                module=None,
                category=documentation.category,
                summary=documentation.summary,
                description=documentation.description,
                parameters=documentation.parameters,
                returns=documentation.returns,
                examples=documentation.examples,
                notes=documentation.notes,
                see_also=documentation.see_also,
                overloads=tuple(
                    _overload_signature(element.name.text, overload, documentation)
                    for overload in element.overloads
                ),
                aliases=tuple(sorted(aliases.get(element.name.text, ()), key=str.casefold)),
                source_path="src/valiance/analysis/builtins.py",
            )
        )
    if strict and missing:
        raise DocumentationError(
            "missing built-in documentation for: " + ", ".join(sorted(missing))
        )
    return tuple(sorted(references, key=_reference_sort_key))


def collect_stdlib_references(*, strict: bool = True) -> tuple[ElementReference, ...]:
    """Collect Python-backed and Valiance-defined standard-library references."""
    references: list[ElementReference] = []
    missing: list[str] = []
    conflicts: list[str] = []

    for module_name, functions in native_stdlib_functions().items():
        grouped_functions: dict[str, list[NativeFunction]] = {}
        for function in functions:
            grouped_functions.setdefault(function.name.text, []).append(function)
        for function_name, overloads in grouped_functions.items():
            qualified_name = f"std.{module_name}.{function_name}"
            documented = {
                function.documentation
                for function in overloads
                if function.documentation is not None
            }
            if not documented:
                missing.append(qualified_name)
                continue
            if len(documented) > 1:
                conflicts.append(qualified_name)
                continue
            documentation = next(iter(documented))
            if any(function.documentation is None for function in overloads):
                missing.append(qualified_name)
                continue
            references.append(_native_reference(module_name, overloads, documentation))

    package_root = resources.files("valiance.std")
    for path in sorted(package_root.iterdir(), key=lambda item: item.name.casefold()):
        if not path.name.endswith(".vlnc"):
            continue
        source = path.read_text(encoding="utf-8")
        module_name = path.name.removesuffix(".vlnc")
        documented = extract_documented_defines(
            source,
            source_path=f"src/valiance/std/{path.name}",
        )
        public_names = {
            str(node.name)
            for node in parse(source)
            if isinstance(node, DefineNode)
            and node.visibility is not None
            and node.visibility.text == "public"
        }
        documented_by_name: dict[str, list[DefinitionReference]] = {}
        for definition in documented:
            if definition.name in public_names:
                documented_by_name.setdefault(definition.name, []).append(definition)
        documented_names = set(documented_by_name)
        for name in sorted(public_names - documented_names, key=str.casefold):
            missing.append(f"std.{module_name}.{name}")
        for function_name, definitions in documented_by_name.items():
            docstrings = {definition.docstring for definition in definitions}
            if len(docstrings) > 1:
                conflicts.append(f"std.{module_name}.{function_name}")
                continue
            references.append(_source_reference(module_name, definitions))

    if strict and (missing or conflicts):
        messages = []
        if missing:
            messages.append(
                "missing standard-library documentation for: "
                + ", ".join(sorted(missing))
            )
        if conflicts:
            messages.append(
                "conflicting standard-library documentation for overloads of: "
                + ", ".join(sorted(conflicts))
            )
        raise DocumentationError("; ".join(messages))
    return tuple(sorted(references, key=_reference_sort_key))


def collect_language_references(*, strict: bool = True) -> tuple[ElementReference, ...]:
    """Collect the complete built-in and standard-library function catalogue."""
    return tuple(
        sorted(
            (
                *collect_builtin_references(strict=strict),
                *collect_stdlib_references(strict=strict),
            ),
            key=_reference_sort_key,
        )
    )


def render_language_reference_html(
    references: Sequence[ElementReference],
    *,
    title: str = "Valiance Built-ins and Standard Library Reference",
) -> str:
    """Render a searchable self-contained HTML language reference."""
    grouped = _group_references(references)
    navigation: list[str] = []
    sections: list[str] = []
    for group_name, group_references in grouped:
        anchor = _slug(group_name)
        navigation.append(
            f'<li><a href="#{html.escape(anchor)}">{html.escape(group_name)}</a></li>'
        )
        cards = "\n".join(_reference_card(item) for item in group_references)
        sections.append(
            f'<section class="reference-group" id="{html.escape(anchor)}">\n'
            f"  <h2>{html.escape(group_name)}</h2>\n{cards}\n</section>"
        )
    return "\n".join(
        (
            "<!doctype html>",
            '<html lang="en">',
            "<head>",
            '  <meta charset="utf-8">',
            '  <meta name="viewport" content="width=device-width, initial-scale=1">',
            f"  <title>{html.escape(title)}</title>",
            "  <style>",
            _LANGUAGE_REFERENCE_CSS,
            "  </style>",
            "</head>",
            "<body>",
            "  <header>",
            f"    <h1>{html.escape(title)}</h1>",
            "    <p>Generated from compiler and standard-library documentation metadata.</p>",
            '    <input id="filter" type="search" placeholder="Filter names, signatures, or descriptions" aria-label="Filter reference">',
            "  </header>",
            '  <div class="layout">',
            '    <nav aria-label="Reference sections"><h2>Sections</h2><ul>',
            *(f"      {item}" for item in navigation),
            "    </ul></nav>",
            "    <main>",
            *(f"      {line}" for section in sections for line in section.splitlines()),
            "    </main>",
            "  </div>",
            "  <script>",
            "    const filter = document.querySelector('#filter');",
            "    filter.addEventListener('input', () => {",
            "      const query = filter.value.toLowerCase();",
            "      document.querySelectorAll('.element').forEach(card => {",
            "        card.hidden = !card.textContent.toLowerCase().includes(query);",
            "      });",
            "    });",
            "  </script>",
            "</body>",
            "</html>",
            "",
        )
    )


def render_language_reference_markdown(
    references: Sequence[ElementReference],
    *,
    title: str = "Valiance Built-ins and Standard Library Reference",
) -> str:
    """Render the language reference as portable Markdown."""
    lines = [f"# {title}", ""]
    for group_name, group_references in _group_references(references):
        lines.extend((f"## {group_name}", ""))
        for reference in group_references:
            lines.extend((f"### `{reference.qualified_name}`", "", reference.summary, ""))
            lines.extend(paragraph for item in reference.description for paragraph in (item, ""))
            if reference.aliases:
                lines.extend((f"**Aliases:** {', '.join(f'`{item}`' for item in reference.aliases)}", ""))
            lines.append("**Overloads**")
            lines.append("")
            lines.extend(f"- `{signature}`" for signature in reference.overloads)
            lines.append("")
            if reference.parameters:
                lines.extend(("**Parameters**", ""))
                lines.extend(
                    f"- `{parameter.name}` — {parameter.description}"
                    for parameter in reference.parameters
                )
                lines.append("")
            if reference.returns is not None:
                lines.extend((f"**Returns:** {reference.returns}", ""))
            for example in reference.examples:
                lines.extend(("**Example**", "", "```vlnc", example.source, "```"))
                if example.result is not None:
                    lines.extend(("Result:", "", "```text", example.result, "```"))
                if example.description:
                    lines.extend((example.description, ""))
            if reference.notes:
                lines.extend(("**Notes**", ""))
                lines.extend(f"- {note}" for note in reference.notes)
                lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_language_reference_json(
    references: Sequence[ElementReference],
    *,
    title: str = "Valiance Built-ins and Standard Library Reference",
) -> str:
    """Render deterministic machine-readable JSON for tooling and websites."""
    payload = {
        "schema_version": REFERENCE_SCHEMA_VERSION,
        "title": title,
        "elements": [asdict(reference) for reference in references],
    }
    return json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n"


def render_language_reference(
    references: Sequence[ElementReference],
    *,
    output_format: str = "html",
    title: str = "Valiance Built-ins and Standard Library Reference",
) -> str:
    """Render a language catalogue in HTML, Markdown, or JSON format."""
    if output_format == "html":
        return render_language_reference_html(references, title=title)
    if output_format == "markdown":
        return render_language_reference_markdown(references, title=title)
    if output_format == "json":
        return render_language_reference_json(references, title=title)
    raise ValueError(f"unsupported documentation format: {output_format}")


def _native_reference(
    module_name: str,
    functions: Sequence[NativeFunction],
    documentation: ElementDocumentation,
) -> ElementReference:
    """Build one reference entry for a Python-backed standard-library export."""
    function = functions[0]
    qualified_name = f"std.{module_name}.{function.name.text}"
    return ElementReference(
        name=function.name.text,
        qualified_name=qualified_name,
        scope="standard-library",
        module=f"std.{module_name}",
        category=documentation.category,
        summary=documentation.summary,
        description=documentation.description,
        parameters=documentation.parameters,
        returns=documentation.returns,
        examples=documentation.examples,
        notes=documentation.notes,
        see_also=documentation.see_also,
        overloads=tuple(
            _overload_signature(
                qualified_name,
                T.Overload(
                    candidate.params,
                    candidate.returns,
                    param_names=candidate.param_names,
                ),
                documentation,
            )
            for candidate in functions
        ),
        source_path=f"src/valiance/std/{module_name}.py",
    )


def _source_reference(
    module_name: str,
    definitions: Sequence[DefinitionReference],
) -> ElementReference:
    """Build one reference entry from a documented Valiance stdlib definition."""
    definition = definitions[0]
    documentation = ElementDocumentation(
        summary=_summary_from_description(definition.docstring.description),
        description=definition.docstring.description,
        parameters=tuple(
            ParameterDocumentation(field.name, field.description)
            for field in definition.docstring.params
        ),
        returns=definition.docstring.returns,
        category=module_name.replace("_", " ").title(),
        notes=definition.docstring.extra_fields,
    )
    qualified_name = f"std.{module_name}.{definition.name}"
    return ElementReference(
        name=definition.name,
        qualified_name=qualified_name,
        scope="standard-library",
        module=f"std.{module_name}",
        category=documentation.category,
        summary=documentation.summary,
        description=documentation.description,
        parameters=documentation.parameters,
        returns=documentation.returns,
        examples=documentation.examples,
        notes=documentation.notes,
        see_also=documentation.see_also,
        overloads=tuple(item.signature for item in definitions),
        source_path=definition.source_path,
    )


def _summary_from_description(lines: Sequence[str]) -> str:
    """Return the first non-empty documentation line as a compact summary."""
    for line in lines:
        if line:
            return line
    return "Documented standard-library function."


def _overload_signature(
    name: str,
    overload: T.Overload,
    documentation: ElementDocumentation,
) -> str:
    """Render one overload with stable parameter labels and generic constraints."""
    names: list[str] = []
    if len(overload.param_names) == len(overload.params):
        names = [
            parameter_name.text if parameter_name is not None else f"input{index + 1}"
            for index, parameter_name in enumerate(overload.param_names)
        ]
    elif len(documentation.parameters) == len(overload.params):
        names = [parameter.name for parameter in documentation.parameters]
    else:
        names = [f"input{index + 1}" for index, _ in enumerate(overload.params)]
    params = ", ".join(
        f"{parameter_name}: {T.show(parameter_type)}"
        for parameter_name, parameter_type in zip(names, overload.params, strict=True)
    )
    returns = ", ".join(T.show(item) for item in overload.returns) or "()"
    signature = f"{name}({params}) -> {returns}"
    if overload.generic_constraints:
        constraints = ", ".join(
            f"{constraint.name}: {T.show(constraint.bound)}"
            for constraint in overload.generic_constraints
        )
        signature += f" where {constraints}"
    return signature


def _reference_sort_key(reference: ElementReference) -> tuple[str, str, str]:
    """Return a stable scope, module, and name ordering for generated output."""
    return (
        reference.scope.casefold(),
        (reference.module or "").casefold(),
        reference.qualified_name.casefold(),
    )


def _group_references(
    references: Sequence[ElementReference],
) -> tuple[tuple[str, tuple[ElementReference, ...]], ...]:
    """Group references into built-in categories and standard-library modules."""
    grouped: dict[str, list[ElementReference]] = {}
    for reference in references:
        if reference.scope == "built-in":
            label = f"Built-ins — {reference.category}"
        else:
            label = reference.module or "Standard library"
        grouped.setdefault(label, []).append(reference)
    return tuple(
        (label, tuple(sorted(items, key=_reference_sort_key)))
        for label, items in sorted(grouped.items(), key=lambda item: item[0].casefold())
    )


def _reference_card(reference: ElementReference) -> str:
    """Render one HTML card for an element reference."""
    searchable = " ".join(
        (
            reference.qualified_name,
            reference.summary,
            *reference.overloads,
            *reference.description,
        )
    )
    lines = [
        f'<article class="element" id="{html.escape(_slug(reference.qualified_name))}" data-search="{html.escape(searchable)}">',
        f"  <h3><code>{html.escape(reference.qualified_name)}</code></h3>",
        f"  <p>{_inline_html(reference.summary)}</p>",
    ]
    if reference.aliases:
        lines.append(
            "  <p><strong>Aliases:</strong> "
            + ", ".join(f"<code>{html.escape(alias)}</code>" for alias in reference.aliases)
            + "</p>"
        )
    lines.extend(
        (
            "  <h4>Overloads</h4>",
            "  <ul class=\"overloads\">",
            *(f"    <li><code>{html.escape(signature)}</code></li>" for signature in reference.overloads),
            "  </ul>",
        )
    )
    lines.extend(f"  <p>{_inline_html(paragraph)}</p>" for paragraph in reference.description)
    if reference.parameters:
        lines.extend(("  <h4>Parameters</h4>", "  <dl>"))
        for parameter in reference.parameters:
            lines.append(f"    <dt><code>{html.escape(parameter.name)}</code></dt>")
            lines.append(f"    <dd>{_inline_html(parameter.description)}</dd>")
        lines.append("  </dl>")
    if reference.returns is not None:
        lines.append(f"  <p><strong>Returns:</strong> {_inline_html(reference.returns)}</p>")
    for example in reference.examples:
        lines.extend(("  <h4>Example</h4>", f"  <pre><code>{html.escape(example.source)}</code></pre>"))
        if example.result is not None:
            lines.append(f"  <p><strong>Result:</strong> <code>{html.escape(example.result)}</code></p>")
        if example.description:
            lines.append(f"  <p>{_inline_html(example.description)}</p>")
    if reference.notes:
        lines.extend(("  <h4>Notes</h4>", "  <ul>"))
        lines.extend(f"    <li>{_inline_html(note)}</li>" for note in reference.notes)
        lines.append("  </ul>")
    if reference.source_path:
        lines.append(f"  <p class=\"source\">Source: <code>{html.escape(reference.source_path)}</code></p>")
    lines.append("</article>")
    return "\n".join(lines)


def _inline_html(value: str) -> str:
    """Render backtick-delimited code spans while escaping other HTML."""
    pieces = re.split(r"(`[^`]+`)", value)
    return "".join(
        f"<code>{html.escape(piece[1:-1])}</code>"
        if piece.startswith("`") and piece.endswith("`")
        else html.escape(piece)
        for piece in pieces
    )


def _slug(value: str) -> str:
    """Return a stable HTML identifier for a reference group or element."""
    slug = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    if slug:
        return slug
    encoded = "-".join(f"{ord(character):x}" for character in value)
    return f"symbol-{encoded}" if encoded else "item"


_LANGUAGE_REFERENCE_CSS = """    :root {
      color-scheme: light dark;
      font-family: Inter, ui-sans-serif, system-ui, sans-serif;
      line-height: 1.55;
    }
    body { margin: 0; background: Canvas; color: CanvasText; }
    header { padding: 2rem clamp(1rem, 5vw, 4rem); border-bottom: 1px solid GrayText; }
    header h1 { margin: 0 0 .35rem; }
    header p { margin: 0 0 1rem; color: GrayText; }
    #filter { box-sizing: border-box; width: min(42rem, 100%); padding: .7rem .85rem; }
    .layout { display: grid; grid-template-columns: minmax(14rem, 20rem) 1fr; gap: 2rem; padding: 2rem clamp(1rem, 5vw, 4rem); }
    nav { position: sticky; top: 1rem; align-self: start; max-height: calc(100vh - 2rem); overflow: auto; }
    nav h2 { font-size: 1rem; text-transform: uppercase; letter-spacing: .08em; }
    nav ul { list-style: none; padding: 0; }
    nav li { margin: .45rem 0; }
    main { min-width: 0; }
    .reference-group { margin-bottom: 3rem; }
    .element { border: 1px solid GrayText; border-radius: .75rem; padding: 1.15rem 1.25rem; margin: 1rem 0; }
    .element h3 { margin-top: 0; font-size: 1.25rem; }
    .element h4 { margin-bottom: .35rem; }
    .overloads { padding-left: 1.4rem; }
    pre { overflow-x: auto; padding: .8rem; background: color-mix(in srgb, CanvasText 8%, Canvas); border-radius: .4rem; }
    dt { font-weight: 700; }
    dd { margin-bottom: .45rem; }
    .source { color: GrayText; font-size: .9rem; }
    a { color: LinkText; }
    [hidden] { display: none !important; }
    @media (max-width: 760px) { .layout { grid-template-columns: 1fr; } nav { position: static; } }
"""
