"""Declaration helpers for Python-backed standard library modules."""

from __future__ import annotations

import importlib
import pkgutil
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import valiance.vtypes as T
from valiance.elements.builtins import BuiltinElement, BuiltinOverload, RuntimeContext
from valiance.elements.documentation import ElementDocumentation
from valiance.asts import (
    DefineNode,
    ElementNode,
    FunctionNode,
    FunctionOverloadTyping,
    FunctionParam,
    GetVariableNode,
    TypedElementNode,
    TypedFunctionNode,
    TypedNode,
)
from valiance.vtypes.symbols import Symbol

NativeImpl = Callable[[tuple[Any, ...], RuntimeContext], tuple[Any, ...]]


@dataclass(frozen=True)
class NativeFunction:
    """One Python-backed function exported by a standard library module."""

    name: Symbol
    params: tuple[T.Type, ...]
    returns: tuple[T.Type, ...]
    implementation: NativeImpl
    param_names: tuple[Symbol, ...] = ()
    documentation: ElementDocumentation | None = None


def native_function(
    name: str,
    params: tuple[T.Type, ...],
    returns: tuple[T.Type, ...],
    implementation: NativeImpl,
    *,
    param_names: tuple[str, ...] = (),
    documentation: ElementDocumentation | None = None,
) -> NativeFunction:
    """Declare one importable Python-backed stdlib function."""
    return NativeFunction(
        Symbol(name),
        params,
        returns,
        implementation,
        tuple(Symbol(item) for item in param_names),
        documentation,
    )


def stdlib_element(
    name: str,
    params: tuple[T.Type, ...],
    returns: tuple[T.Type, ...],
    *,
    param_names: tuple[str, ...] = (),
    documentation: ElementDocumentation | None = None,
) -> Callable[[NativeImpl], NativeImpl]:
    """Decorate a Python implementation as an importable stdlib function."""

    def register(fn: NativeImpl) -> NativeImpl:
        """Record the decorated native function as a standard-library export."""
        fn.__valiance_native_function__ = native_function(
            name,
            params,
            returns,
            fn,
            param_names=param_names,
            documentation=documentation,
        )
        return fn

    return register


def native_module_exports(module_name: str) -> object | None:
    """Return ModuleExports for a Python-backed stdlib module, if it exists."""
    functions = _native_functions(module_name)
    if functions is None:
        return None
    from valiance.modules_system.modules import ModuleExports

    return ModuleExports(
        f"std.{module_name}",
        tuple(_module_definition(module_name, function) for function in functions),
    )


def install_native_stdlib(env: T.Environment, module_name: str) -> T.Environment:
    """Install native hooks while analysing a mixed Valiance/Python std module."""
    functions = _native_functions(module_name) or ()
    for function in functions:
        element = _runtime_element(module_name, function)
        for overload in element.overloads:
            env.define_overload(element.name, overload)
    return env


def runtime_stdlib_elements() -> dict[str, BuiltinElement]:
    """Return every Python-backed stdlib function for the VM's runtime globals."""
    result: dict[str, BuiltinElement] = {}
    for module_name, functions in _all_native_modules().items():
        for function in functions:
            element = _runtime_element(module_name, function)
            result[element.name.dotted()] = element
    return result


def native_stdlib_functions() -> dict[str, tuple[NativeFunction, ...]]:
    """Return documented native exports grouped by standard-library module."""
    return _all_native_modules()


def _module_definition(module_name: str, function: NativeFunction):
    """Build the definition for module for native standard-library registration."""
    from valiance.modules_system.modules import ModuleDefinition

    return ModuleDefinition(
        function.name,
        _typed_wrapper(module_name, function),
        public=True,
    )


def _typed_wrapper(module_name: str, function: NativeFunction) -> TypedFunctionNode:
    """Compute typed wrapper for native standard-library registration."""
    param_names = _param_names(function)
    runtime_name = Symbol(function.name.text, ("std", module_name))
    overload = T.Overload(function.params, function.returns, param_names=param_names)
    applied = T.AppliedOverload(
        overload,
        {},
        function.params,
        function.returns,
        function.returns,
        (),
    )
    typed_body: tuple[TypedNode, ...] = (
        *(
            TypedNode(GetVariableNode(name), typ)
            for name, typ in zip(param_names, function.params, strict=True)
        ),
        TypedElementNode(
            ElementNode(runtime_name),
            function.returns[-1] if function.returns else None,
            applied,
            0,
        ),
    )
    function_type = T.Fn(function.params, function.returns)
    function_node = FunctionNode(
        params=tuple(
            FunctionParam(name, typ)
            for name, typ in zip(param_names, function.params, strict=True)
        ),
        body=tuple(node.node for node in typed_body),
        returns=function.returns,
    )
    define_node = DefineNode(function.name, function_node, visibility=Symbol("public"))
    return TypedFunctionNode(
        define_node,
        function_type,
        (
            FunctionOverloadTyping(
                function_type,
                typed_body,
                overload,
            ),
        ),
    )


def _runtime_element(module_name: str, function: NativeFunction) -> BuiltinElement:
    """Compute runtime element for native standard-library registration."""
    return BuiltinElement(
        Symbol(function.name.text, ("std", module_name)),
        (
            BuiltinOverload(
                T.Overload(function.params, function.returns),
                function.implementation,
            ),
        ),
        function.documentation,
    )


def _param_names(function: NativeFunction) -> tuple[Symbol, ...]:
    """Collect the names for param for native standard-library registration."""
    if function.param_names:
        return function.param_names
    return tuple(Symbol(f"_{index}") for index, _ in enumerate(function.params))


def _native_functions(module_name: str) -> tuple[NativeFunction, ...] | None:
    """Compute native functions for native standard-library registration."""
    try:
        module = importlib.import_module(f"valiance.std.{module_name}")
    except ModuleNotFoundError as exc:
        if exc.name == f"valiance.std.{module_name}":
            return None
        raise
    functions = getattr(module, "NATIVE_FUNCTIONS", None)
    if functions is None:
        functions = tuple(
            function
            for value in module.__dict__.values()
            if (function := getattr(value, "__valiance_native_function__", None))
            is not None
        )
    if not functions:
        return None
    return tuple(functions)


def _all_native_modules() -> dict[str, tuple[NativeFunction, ...]]:
    """Compute all native modules for native standard-library registration."""
    import valiance.std as std_package

    result: dict[str, tuple[NativeFunction, ...]] = {}
    for module in pkgutil.iter_modules(std_package.__path__):
        if module.name.startswith("_"):
            continue
        functions = _native_functions(module.name)
        if functions:
            result[module.name] = functions
    return result
