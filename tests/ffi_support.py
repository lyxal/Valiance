"""Compile portable C fixtures for FFI integration tests on supported hosts."""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory


def shared_library_path(directory: str, name: str) -> str:
    """Return a platform-specific library path safe in Valiance string literals."""
    if os.name == "nt":
        extension = ".dll"
    elif sys.platform == "darwin":
        extension = ".dylib"
    else:
        extension = ".so"
    # The lexer interprets backslashes in string literals. Windows accepts
    # forward slashes for absolute DLL filenames, including through LoadLibrary.
    return (Path(directory) / f"lib{name}{extension}").as_posix()


def build_shared_library(source: str, library: str, *, threads: bool = False) -> None:
    """Build a test DLL/dylib/so with exported cdecl functions.

    Requires a C compiler on PATH (or a CC environment override). Windows
    supports Visual Studio's cl/clang-cl and MinGW/LLVM's GCC/Clang.
    """
    specified = os.environ.get("CC")
    if specified:
        command = shlex.split(specified, posix=os.name != "nt")
        if command:
            command[0] = command[0].strip('"')
        if not command:
            raise RuntimeError("CC does not name a C compiler")
    else:
        if os.name == "nt":
            candidates = ("cl", "clang-cl", "clang", "gcc", "cc")
        else:
            candidates = ("cc", "clang", "gcc")
        compiler_path = next((name for name in candidates if shutil.which(name)), None)
        if compiler_path is None:
            raise RuntimeError(
                "FFI integration tests require a C compiler (set CC or install one)"
            )
        command = [compiler_path]

    compiler = Path(command[0]).stem.lower()
    msvc = compiler in {"cl", "clang-cl"}
    export = "__declspec(dllexport)" if os.name == "nt" else ""
    if msvc:
        args = [*command, "/nologo", "/LD"]
        if compiler == "cl":
            args.append("/std:c11")
        args.extend([f"/DFFI_EXPORT={export}", source, "/link", f"/OUT:{library}"])
    else:
        args = [*command, "-dynamiclib" if sys.platform == "darwin" else "-shared"]
        if os.name != "nt" and sys.platform != "darwin":
            args.append("-fPIC")
        if threads and os.name != "nt":
            args.append("-pthread")
        args.extend([f"-DFFI_EXPORT={export}", source, "-o", library])
    completed = subprocess.run(
        args, cwd=str(Path(source).parent), capture_output=True, text=True
    )
    if completed.returncode:
        raise RuntimeError(
            f"C fixture compilation failed ({completed.returncode}):\n"
            f"{completed.stdout}{completed.stderr}"
        )


@contextmanager
def native_library_directory() -> Iterator[str]:
    """Remove a fixture DLL after native calls finish, including on Windows.

    The VM intentionally keeps process-wide ctypes library/function caches.
    Windows locks a DLL file for as long as LoadLibrary holds a reference, so
    tests must clear *their own* cache entries and release the DLL explicitly
    before TemporaryDirectory can delete the compiled fixture.
    """
    with TemporaryDirectory() as directory:
        try:
            yield directory
        finally:
            if os.name == "nt":
                _unload_windows_fixture_libraries(directory)


def _unload_windows_fixture_libraries(directory: str) -> None:
    """Release only cached fixture DLLs inside one finished test directory."""
    import ctypes

    from valiance.runtime import vm as runtime_vm

    directory = os.path.normcase(os.path.abspath(directory))

    def in_fixture(path: str | None) -> bool:
        return bool(
            path and os.path.normcase(os.path.dirname(os.path.abspath(path))) == directory
        )

    with runtime_vm._NATIVE_CACHE_LOCK:
        for key in tuple(runtime_vm._NATIVE_FUNCTION_CACHE):
            if in_fixture(key[0]):
                del runtime_vm._NATIVE_FUNCTION_CACHE[key]
        libraries = [
            runtime_vm._NATIVE_LIBRARY_CACHE.pop(key)
            for key in tuple(runtime_vm._NATIVE_LIBRARY_CACHE)
            if in_fixture(key)
        ]

    free_library = ctypes.WinDLL("kernel32", use_last_error=True).FreeLibrary
    free_library.argtypes = (ctypes.c_void_p,)
    free_library.restype = ctypes.c_int
    for library in libraries:
        if not free_library(ctypes.c_void_p(library._handle)):
            raise ctypes.WinError(ctypes.get_last_error())
