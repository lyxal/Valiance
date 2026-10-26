"""Collect and render built-in and standard-library reference documentation."""

from __future__ import annotations

import html
import json
import re
from dataclasses import asdict, dataclass, fields, replace
from importlib import resources
from typing import Sequence

import valiance.vtypes as T
from valiance.elements.builtins import BUILTIN_ELEMENTS, BuiltinElement
from valiance.asts import DefineNode
from valiance.elements.documentation import (
    DocumentationExample,
    ElementDocumentation,
    ParameterDocumentation,
)
from valiance.parsing import parse
from valiance.source_tools import DefinitionReference, extract_documented_defines
from valiance.elements.stdlib_native import NativeFunction, native_stdlib_functions

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
    overload_documentation: tuple[ElementDocumentation, ...] = ()
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
                    _overload_signature(
                        element.name.text,
                        definition.signature,
                        definition.documentation or documentation,
                    )
                    for definition in element.definitions
                ),
                overload_documentation=tuple(
                    definition.documentation or documentation
                    for definition in element.definitions
                ),
                aliases=tuple(sorted(aliases.get(element.name.text, ()), key=str.casefold)),
                source_path="src/valiance/elements/builtins.py",
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
            if not function.exported:
                continue
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
        for definitions in documented_by_name.values():
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



def collect_concurrency_references() -> tuple[ElementReference, ...]:
    """Document compiler-level concurrency forms alongside callable elements."""
    entries = (
        ("concurrent", "concurrent => ... end", "Run a structured task scope and join its children."),
        ("spawn", "spawn(function) -> Task[outputs...]", "Create a cooperatively scheduled task in the current scope."),
        ("wait", "wait(Task[T...]) -> T...", "Observe a task result; observation is repeatable."),
        ("Channel", "Channel[T] | Int Channel[T] -> Channel[T]", "Create an invariant unbuffered or bounded FIFO channel."),
        ("send", "send(Channel[T], T) ->", "Send one transferable value with rendezvous or backpressure."),
        ("receive", "receive(Channel[T]) -> Receive[T]", "Receive Value(value) or Closed without conflating None."),
        ("close", "close(Channel[T]) ->", "Close a channel; buffered values remain drainable."),
        ("Receive.Value", "Receive.Value(T)", "A channel receive containing a transmitted value."),
        ("Receive.Closed", "Receive.Closed", "A channel receive reporting closed and fully drained."),
    )
    examples = {
        "concurrent": "concurrent => fn => 42 end | spawn | wait end",
        "spawn": "$task = fn => 42 end | spawn\n$task wait",
        "wait": "$task = fn => 42 end | spawn\n$task wait",
        "Channel": "$channel = 1 Channel[Int]\n$channel 7 send\n$channel receive",
        "send": "$channel = 1 Channel[Int]\n$channel 7 send\n$channel receive",
        "receive": "$channel = 1 Channel[Int]\n$channel 7 send\n$channel receive",
        "close": "$channel = Channel[Int]\n$channel close\n$channel receive",
        "Receive.Value": "$channel = 1 Channel[Int]\n$channel 7 send\n$channel receive",
        "Receive.Closed": "$channel = Channel[Int]\n$channel close\n$channel receive",
    }
    return tuple(
        ElementReference(
            name=name,
            qualified_name=name,
            scope="language",
            module=None,
            category="Concurrency",
            summary=summary,
            description=(),
            parameters=(),
            returns=None,
            examples=(DocumentationExample(examples[name]),),
            notes=(),
            see_also=("docs/language.md#23-concurrency-runtime-contracts",),
            overloads=(signature,),
            overload_documentation=(ElementDocumentation(
                summary=summary, examples=(DocumentationExample(examples[name]),),
                category="Concurrency",
            ),),
        )
        for name, signature, summary in entries
    )

def collect_language_references(*, strict: bool = True) -> tuple[ElementReference, ...]:
    """Collect the complete built-in and standard-library function catalogue."""
    return tuple(
        sorted(
            (
                *collect_builtin_references(strict=strict),
                *collect_stdlib_references(strict=strict),
                *collect_concurrency_references(),
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
    links = {
        name: _element_anchor(reference.qualified_name)
        for reference in references
        for name in (reference.qualified_name, *reference.aliases)
    }
    navigation: dict[str, list[str]] = {}
    sections: list[str] = []
    for group_name, group_references in grouped:
        anchor = "group-" + _slug(group_name)
        scope = group_references[0].scope
        label = group_references[0].module or group_references[0].category
        entries = "".join(
            f'<li data-entry="{_element_anchor(item.qualified_name)}">'
            f'<a href="#{_element_anchor(item.qualified_name)}" '
            f'title="{html.escape(item.summary)}"><code>{html.escape(item.name)}</code></a></li>'
            for item in group_references
        )
        navigation.setdefault(scope, []).append(
            f'<li class="nav-group" data-section="{anchor}"><details>'
            f'<summary><span>{html.escape(label)}</span>'
            f'<span class="nav-count">{len(group_references)}</span></summary>'
            f'<a class="nav-overview" href="#{anchor}">Overview</a>'
            f'<ul>{entries}</ul></details></li>'
        )
        cards = "\n".join(_reference_card(item, links) for item in group_references)
        sections.append(
            f'<section class="reference-group" id="{anchor}">\n'
            f'<div class="section-heading"><h2>{html.escape(group_name)}</h2>'
            f'<span>{len(group_references)} entries</span></div>\n{cards}\n</section>'
        )
    assets = resources.files("valiance.elements")
    stylesheet = assets.joinpath("reference_docs.css").read_text(encoding="utf-8")
    script = assets.joinpath("reference_docs.js").read_text(encoding="utf-8")
    return "\n".join(
        (
            "<!doctype html>",
            '<html lang="en">',
            '<head><meta charset="utf-8">',
            '<meta name="viewport" content="width=device-width, initial-scale=1">',
            f"<title>{html.escape(title)}</title>",
            f"<style>{stylesheet}</style></head>",
            '<body><a class="skip-link" href="#reference">Skip to reference</a>',
            '<div class="layout">',
            '<aside><a class="brand" href="#top"><span class="brand-mark">V</span> Valiance</a>',
            '<p class="nav-label">LANGUAGE REFERENCE</p>',
            '<nav aria-label="Reference sections"><ul>',
            *(
                f'<li class="nav-scope" data-scope="{scope}"><details open>'
                f'<summary>{label}</summary><ul>{"".join(navigation[scope])}</ul></details></li>'
                for scope, label in (
                    ("built-in", "Built-ins"),
                    ("standard-library", "Standard library"),
                    ("language", "Language forms"),
                )
                if scope in navigation
            ),
            '</ul></nav><p class="sidebar-note">Built from language declarations.<br>Available offline.</p></aside>',
            '<main id="reference"><header id="top">',
            '<p class="eyebrow">THE VALIANCE LANGUAGE</p>',
            f'<h1>{html.escape(title)}</h1>',
            '<p class="intro">Explore the building blocks. Find a function, inspect its stack signature, '
            'and put it to work.</p>',
            f'<p class="catalogue-count">{len(references)} entries · Built-ins · Standard library · Language forms</p>',
            '</header>',
            '<div class="search-panel" hidden>',
            '<label for="filter">Search the reference</label>',
            '<div class="search-row"><input id="filter" type="search" '
            'placeholder="Name, alias, type, or description…" autocomplete="off" '
            'aria-controls="entries"><button id="clear" type="button">Clear</button></div>',
            '<div class="filter-row"><label for="scope">Show</label><select id="scope">'
            '<option value="all">All entries</option><option value="built-in">Built-ins</option>'
            '<option value="standard-library">Standard library</option>'
            '<option value="language">Language forms</option></select>'
            '<span class="search-hint">Press / to search · Esc to clear</span></div>',
            f'<p id="result-count" role="status" aria-live="polite">{len(references)} entries</p>',
            '</div><div id="entries">',
            *sections,
            '</div><div id="empty-state" hidden><h2>No entries found</h2>'
            '<p>Try a shorter search or choose a different scope.</p></div>',
            '<footer>Valiance reference · Generated from compiler and standard library metadata</footer>',
            '</main></div>',
            f'<script>{script}</script>',
            '</body></html>',
            '',
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
            if reference.aliases:
                lines.extend((f"**Aliases:** {', '.join(f'`{item}`' for item in reference.aliases)}", ""))
            lines.append("**Overloads**")
            lines.append("")
            for index, signature in enumerate(reference.overloads):
                lines.append(f"- `{signature}`")
                if index < len(reference.overload_documentation):
                    overload_doc = reference.overload_documentation[index]
                    lines.append("")
                    if _distinct_overload_documentation(reference, overload_doc):
                        lines.extend((f"  {overload_doc.summary}", ""))
                    lines.extend(
                        "  " + line if line else ""
                        for line in _documentation_markdown(_overload_details(reference, overload_doc))
                    )
            lines.append("")
            lines.extend(_documentation_markdown(reference))
    return "\n".join(lines).rstrip() + "\n"


def _overload_details(
    reference: ElementReference, documentation: ElementDocumentation
) -> ElementDocumentation:
    """Show shared examples per signature without repeating shared parameter prose."""
    if _distinct_overload_documentation(reference, documentation):
        return documentation
    return replace(documentation, description=(), parameters=(), returns=None, notes=())


def _distinct_overload_documentation(
    reference: ElementReference, documentation: ElementDocumentation
) -> bool:
    """Check whether an overload adds details beyond its element overview."""
    return any(
        getattr(reference, field.name) != getattr(documentation, field.name)
        for field in fields(ElementDocumentation)
    )


def _documentation_markdown(documentation: ElementDocumentation | ElementReference) -> list[str]:
    """Render detailed metadata consistently for element overviews and overloads."""
    lines: list[str] = []
    lines.extend(paragraph for item in documentation.description for paragraph in (item, ""))
    if documentation.parameters:
        lines.extend(("**Parameters**", ""))
        lines.extend(
            f"- `{parameter.name}` — {parameter.description}"
            for parameter in documentation.parameters
        )
        lines.append("")
    if documentation.returns is not None:
        lines.extend((f"**Returns:** {documentation.returns}", ""))
    for example in documentation.examples:
        lines.extend(("**Example**", "", "```vlnc", example.source, "```"))
        if example.result is not None:
            lines.extend(("Result:", "", "```text", example.result, "```"))
        if example.description:
            lines.append(example.description)
        lines.append("")
    if documentation.notes:
        lines.extend(("**Notes**", ""))
        lines.extend(f"- {note}" for note in documentation.notes)
        lines.append("")
    return lines


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
        overload_documentation=tuple(
            candidate.documentation or documentation for candidate in functions
        ),
        source_path=f"src/valiance/std/{module_name}.py",
    )


def _source_reference(
    module_name: str,
    definitions: Sequence[DefinitionReference],
) -> ElementReference:
    """Build one reference entry from a documented Valiance stdlib definition."""
    definition = definitions[0]
    overload_documentation = tuple(_source_documentation(module_name, item) for item in definitions)
    documentation = overload_documentation[0]
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
        overload_documentation=overload_documentation,
        source_path=definition.source_path,
    )


def _source_documentation(module_name: str, definition: DefinitionReference) -> ElementDocumentation:
    """Retain documentation and examples for one source-language overload."""
    return ElementDocumentation(
        summary=_summary_from_description(definition.docstring.description),
        description=definition.docstring.description,
        parameters=tuple(
            ParameterDocumentation(field.name, field.description)
            for field in definition.docstring.params
        ),
        returns=definition.docstring.returns,
        category=module_name.replace("_", " ").title(),
        examples=tuple(
            DocumentationExample(field.removeprefix("@example "))
            for field in definition.docstring.extra_fields
            if field.startswith("@example ")
        ),
        notes=tuple(
            field for field in definition.docstring.extra_fields
            if not field.startswith("@example ")
        ),
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
        elif reference.scope == "language":
            label = f"Language — {reference.category}"
        else:
            label = reference.module or "Standard library"
        grouped.setdefault(label, []).append(reference)
    return tuple(
        (label, tuple(sorted(items, key=_reference_sort_key)))
        for label, items in sorted(grouped.items(), key=lambda item: item[0].casefold())
    )


def _reference_card(reference: ElementReference, links: dict[str, str]) -> str:
    """Render one HTML card for an element reference."""
    anchor = _element_anchor(reference.qualified_name)
    lines = [
        f'<article class="element" id="{anchor}" data-scope="{html.escape(reference.scope)}">',
        '<div class="element-heading">',
        f'<h3><a class="permalink" href="#{anchor}"><code>{html.escape(reference.qualified_name)}</code></a></h3>',
        f'<span class="badge">{html.escape(reference.category)}</span></div>',
        f'<p class="summary">{_inline_html(reference.summary)}</p>',
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
            *(
                "    <li><code>"
                + html.escape(signature)
                + "</code>"
                + (
                    f"<p>{_inline_html(reference.overload_documentation[index].summary)}</p>"
                    + "\n".join(_documentation_html(_overload_details(reference, reference.overload_documentation[index])))
                    if index < len(reference.overload_documentation)
                    else ""
                )
                + "</li>"
                for index, signature in enumerate(reference.overloads)
            ),
            "  </ul>",
        )
    )
    lines.extend(_documentation_html(reference))
    if reference.see_also:
        related = []
        for name in reference.see_also:
            label = f"<code>{html.escape(name)}</code>"
            target = links.get(name) or links.get(f"{reference.module}.{name}")
            related.append(f'<a href="#{target}">{label}</a>' if target else label)
        lines.append('<p class="related"><strong>See also:</strong> ' + ", ".join(related) + "</p>")
    if reference.source_path:
        lines.append(f"  <p class=\"source\">Source: <code>{html.escape(reference.source_path)}</code></p>")
    lines.append("</article>")
    return "\n".join(lines)


def _documentation_html(documentation: ElementDocumentation | ElementReference) -> list[str]:
    """Render the prose, inputs, results and examples belonging to one signature."""
    lines: list[str] = []
    lines.extend(
        f"  <p>{_inline_html(paragraph)}</p>"
        for paragraph in documentation.description
        if paragraph != documentation.summary
    )
    if documentation.parameters:
        lines.extend(("  <h4>Parameters</h4>", "  <dl>"))
        for parameter in documentation.parameters:
            lines.append(f"    <dt><code>{html.escape(parameter.name)}</code></dt>")
            lines.append(f"    <dd>{_inline_html(parameter.description)}</dd>")
        lines.append("  </dl>")
    if documentation.returns is not None:
        lines.append(f"  <p><strong>Returns:</strong> {_inline_html(documentation.returns)}</p>")
    for example in documentation.examples:
        lines.extend(("  <h4>Example</h4>", f"  <pre><code>{html.escape(example.source)}</code></pre>"))
        if example.result is not None:
            lines.append(f"  <p><strong>Result:</strong> <code>{html.escape(example.result)}</code></p>")
        if example.description:
            lines.append(f"  <p>{_inline_html(example.description)}</p>")
    if documentation.notes:
        lines.extend(("  <h4>Notes</h4>", "  <ul>"))
        lines.extend(f"    <li>{_inline_html(note)}</li>" for note in documentation.notes)
        lines.append("  </ul>")
    return lines


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
    """Return a readable group identifier with an exact-name collision suffix."""
    slug = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    return f"{slug or 'group'}-{value.encode('utf-8').hex()}"


def _element_anchor(qualified_name: str) -> str:
    """Encode exact names into stable anchors, including symbolic operators."""
    return "element-" + qualified_name.encode("utf-8").hex()
