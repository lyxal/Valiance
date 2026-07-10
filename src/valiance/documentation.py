"""Shared documentation metadata for built-ins and standard-library elements."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ParameterDocumentation:
    """Human-facing documentation for one logical element input."""

    name: str
    description: str


@dataclass(frozen=True, slots=True)
class DocumentationExample:
    """One executable-looking example shown in generated reference material."""

    source: str
    result: str | None = None
    description: str | None = None


@dataclass(frozen=True, slots=True)
class ElementDocumentation:
    """Human-facing metadata shared by all overloads of one element."""

    summary: str
    description: tuple[str, ...] = ()
    parameters: tuple[ParameterDocumentation, ...] = ()
    returns: str | None = None
    examples: tuple[DocumentationExample, ...] = ()
    category: str = "General"
    notes: tuple[str, ...] = ()
    see_also: tuple[str, ...] = ()


def element_documentation(
    summary: str,
    *,
    description: str | tuple[str, ...] = (),
    parameters: tuple[tuple[str, str], ...] = (),
    returns: str | None = None,
    examples: tuple[
        str | tuple[str, str | None] | tuple[str, str | None, str | None], ...
    ] = (),
    category: str = "General",
    notes: str | tuple[str, ...] = (),
    see_also: tuple[str, ...] = (),
) -> ElementDocumentation:
    """Build normalized element documentation with concise declaration syntax."""
    normalized_description = (
        (description,) if isinstance(description, str) else tuple(description)
    )
    normalized_notes = (notes,) if isinstance(notes, str) else tuple(notes)
    normalized_examples: list[DocumentationExample] = []
    for example in examples:
        if isinstance(example, str):
            normalized_examples.append(DocumentationExample(example))
            continue
        if len(example) == 2:
            source, result = example
            normalized_examples.append(DocumentationExample(source, result))
            continue
        source, result, example_description = example
        normalized_examples.append(
            DocumentationExample(source, result, example_description)
        )
    return ElementDocumentation(
        summary=summary,
        description=normalized_description,
        parameters=tuple(
            ParameterDocumentation(name, parameter_description)
            for name, parameter_description in parameters
        ),
        returns=returns,
        examples=tuple(normalized_examples),
        category=category,
        notes=normalized_notes,
        see_also=see_also,
    )
