"""Spawn-process feasibility probe using the shared transactional session service."""

from __future__ import annotations

import _thread
import ctypes
import io
import multiprocessing as mp
import os
import queue
import signal
import sys
import threading
import time
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
from multiprocessing.connection import Connection

from valiance.sessions.events import (
    DiagnosticEvent,
    OutputEvent,
    ResultEvent,
    SessionRequest,
)


@dataclass(frozen=True)
class ProbeEvent:
    """Small presentation-only IPC record; never contains live compiler objects."""

    kind: str
    text: str = ""
    request: int = 0


class _Output(io.TextIOBase):
    """Drop output instead of blocking execution or growing memory without bound."""

    def __init__(self, output, dropped, request: int, kind: str) -> None:
        self.output, self.dropped = output, dropped
        self.request, self.kind = request, kind

    def write(self, text: str) -> int:
        for offset in range(0, len(text), 2048):
            try:
                self.output.put_nowait(
                    ProbeEvent(self.kind, text[offset : offset + 2048], self.request)
                )
            except queue.Full:
                with self.dropped.get_lock():
                    self.dropped.value += 1
        return len(text)


class _Input(io.TextIOBase):
    """Route program input separately from commands and output."""

    def __init__(self, inputs, events: Connection, request: int) -> None:
        self.inputs, self.events, self.request = inputs, events, request

    def readline(self, size: int = -1) -> str:
        self.events.send(
            ProbeEvent("input", "Program is waiting for input", self.request)
        )
        return self.inputs.get()


def _native_block() -> None:
    """Prove a real blocking C call cannot be rescued by Python worker cancellation."""
    if os.name == "nt":
        ctypes.WinDLL("kernel32").Sleep(60_000)
    else:
        ctypes.CDLL(None).sleep(60)


def _serve(commands, inputs, output, events, stop, dropped) -> None:
    """Own shared session mechanics and adapt immutable events to probe display."""
    from valiance.analysis.diagnostics import render
    from valiance.sessions.service import SessionService

    request = 0
    result = ""

    def present(event) -> None:
        """Bound display traffic without inspecting mutable runtime objects."""
        nonlocal result
        if isinstance(event, OutputEvent):
            _Output(output, dropped, request, "output").write(event.text)
        elif isinstance(event, DiagnosticEvent):
            _Output(output, dropped, request, "diagnostic").write(
                render(event.diagnostic, source=event.source, color=False) + "\n"
            )
        elif isinstance(event, ResultEvent):
            result = "\n".join(value[:2048] for value in event.values[:16])

    session = SessionService(event_sink=present)
    stopping = False
    # Launchers may ignore SIGINT; the child must explicitly own its interrupt.
    signal.signal(signal.SIGINT, signal.default_int_handler)

    def interrupt() -> None:
        stop.wait()
        _thread.interrupt_main()

    threading.Thread(target=interrupt, daemon=True).start()
    events.send(ProbeEvent("ready"))
    try:
        while True:
            message = commands.get()
            if isinstance(message, SessionRequest):
                request, operation, source = (
                    message.request_id,
                    message.operation,
                    message.source,
                )
            else:
                request, operation, source = message  # Development-only native probe.
            result = ""
            if operation == "shutdown":
                break
            events.send(ProbeEvent("started", operation, request))
            old_stdin = sys.stdin
            sys.stdin = _Input(inputs, events, request)
            try:
                with (
                    redirect_stdout(_Output(output, dropped, request, "output")),
                    redirect_stderr(_Output(output, dropped, request, "diagnostic")),
                ):
                    if operation == "native-block":
                        _native_block()
                        succeeded = True
                    else:
                        succeeded = session.run(source).successful
                events.send(
                    ProbeEvent("finished" if succeeded else "failed", result, request)
                )
            finally:
                sys.stdin = old_stdin
    except KeyboardInterrupt:
        stopping = True
    finally:
        session.close()
        # Interrupt acknowledges a Python boundary, not complete native cleanup.
        events.send(ProbeEvent("interrupted" if stopping else "closed"))
        output.cancel_join_thread()
        events.close()


class SessionProbe:
    """Own bounded channels and a replaceable persistent session process."""

    def __init__(self) -> None:
        self.context = mp.get_context("spawn")
        self.process = None
        self.busy = False
        self.ready = False
        self.request = 0
        self._reported_drops = 0
        self._close_lock = threading.Lock()

    def start(self) -> None:
        """Spawn explicitly so import and packaging behavior matches Windows."""
        if self.process is not None:
            raise RuntimeError("worker already started")
        self.commands = self.context.Queue(maxsize=1)
        self.inputs = self.context.Queue(maxsize=1)
        self.output = self.context.Queue(maxsize=128)
        self.events, writer = self.context.Pipe(duplex=False)
        self.stop_signal = self.context.Event()
        self.dropped = self.context.Value("Q", 0)
        self.process = self.context.Process(
            target=_serve,
            args=(
                self.commands,
                self.inputs,
                self.output,
                writer,
                self.stop_signal,
                self.dropped,
            ),
            name="valiance-session-probe",
        )
        self.process.start()
        writer.close()

    def submit(self, source: str = "", *, operation: str = "run") -> int:
        """Reject overlapping requests instead of queuing another load."""
        if not self.ready or self.busy or self.process is None:
            raise RuntimeError("worker is not ready for a submission")
        if operation not in {"run", "native-block"}:
            raise ValueError(f"unsupported probe operation: {operation}")
        self.request += 1
        message = (
            (self.request, operation, source)
            if operation == "native-block"
            else (SessionRequest(self.request, "command", source))
        )
        self.commands.put_nowait(message)
        self.busy = True
        return self.request

    def provide_input(self, text: str) -> None:
        """Supply one program-input line without turning it into a REPL command."""
        self.inputs.put_nowait(text.rstrip("\n") + "\n")

    def poll(self) -> list[ProbeEvent]:
        """Read control first and cap each output batch to preserve redraw time."""
        if self.process is None:
            return []
        result = []
        try:
            while self.events.poll():
                event = self.events.recv()
                result.append(event)
                if event.kind == "ready":
                    self.ready = True
                if event.kind in {"finished", "failed", "interrupted", "closed"}:
                    self.busy = False
        except EOFError, BrokenPipeError:
            self.ready = False
            self.busy = False
        for _ in range(64):
            try:
                result.append(self.output.get_nowait())
            except queue.Empty:
                break
        with self.dropped.get_lock():
            dropped = self.dropped.value
        if dropped > self._reported_drops:
            result.append(ProbeEvent("truncated", f"Dropped {dropped} output chunks"))
            self._reported_drops = dropped
        return result

    def close(self, *, grace: float = 0.3) -> str:
        """Try interruption, then bound exit using process termination and join.

        The development probe allows no external child-process ownership. Forced
        termination forfeits language/native cleanup and always discards state.
        """
        with self._close_lock:
            return self._close(grace)

    def _close(self, grace: float) -> str:
        """Serialize Stop and unmount so their joins cannot race each other."""
        if self.process is None:
            return "closed"
        process = self.process
        if process.is_alive():
            if self.busy:
                self.stop_signal.set()
            else:
                self.commands.put_nowait(SessionRequest(0, "shutdown"))
            process.join(grace)
        disposition = "interrupted" if self.busy else "closed"
        if process.is_alive():
            process.terminate()
            process.join(1)
            disposition = "terminated"
        if process.is_alive():
            process.kill()
            process.join(1)
        if process.is_alive():
            raise RuntimeError("session probe process did not exit")
        self.events.close()
        for channel in (self.commands, self.inputs, self.output):
            channel.cancel_join_thread()
            channel.close()
        process.close()
        self.process = None
        self.ready = self.busy = False
        return disposition


def wait_for(
    probe: SessionProbe, kind: str, *, timeout: float = 10
) -> list[ProbeEvent]:
    """Collect bounded probe events until an expected boundary or a timeout."""
    collected = []
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        events = probe.poll()
        collected.extend(events)
        if any(event.kind == kind for event in events):
            return collected
        time.sleep(0.01)
    raise TimeoutError(f"worker did not report {kind!r}; received {collected[-4:]}")
