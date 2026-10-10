"""Bounded process transport for persistent terminal sessions."""

from __future__ import annotations

import _thread
import io
import multiprocessing as mp
import queue
import signal
import sys
import threading
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
from multiprocessing.connection import Connection

from valiance.sessions.events import (
    DiagnosticEvent,
    OutputEvent,
    ResultEvent,
    SessionRequest,
    SessionStarted,
)


@dataclass(frozen=True)
class WorkerEvent:
    """Small presentation-only IPC record; never contains live compiler objects."""

    kind: str
    text: str = ""
    request: int = 0
    sequence: int = 0


class _Output(io.TextIOBase):
    """Drop output instead of blocking execution or growing memory without bound."""

    def __init__(self, output, dropped, request: int, kind: str, written) -> None:
        """Retain bounded output ownership and request correlation."""
        self.output, self.dropped = output, dropped
        self.request, self.kind = request, kind
        self.written = written

    def write(self, text: str) -> int:
        """Send bounded chunks without waiting for the frontend to redraw."""
        for offset in range(0, len(text), 2048):
            try:
                self.output.put_nowait(
                    WorkerEvent(self.kind, text[offset : offset + 2048], self.request)
                )
                self.written[0] += 1
            except queue.Full:
                with self.dropped.get_lock():
                    self.dropped.value += 1
        return len(text)


class _Input(io.TextIOBase):
    """Route program input separately from commands and output."""

    def __init__(self, inputs, events: Connection, request: int) -> None:
        """Retain the input and lifecycle channels independently of stdout."""
        self.inputs, self.events, self.request = inputs, events, request

    def readline(self, size: int = -1) -> str:
        """Request one input line from the frontend."""
        self.events.send(
            WorkerEvent("input", "Program is waiting for input", self.request)
        )
        return self.inputs.get()


def _serve(commands, inputs, output, events, stop, dropped) -> None:
    """Own shared session mechanics and adapt immutable events to terminal display."""
    from valiance.analysis.diagnostics import render
    from valiance.sessions.service import SessionService

    request = 0
    result = ""
    written = [0]

    def present(event) -> None:
        """Bound display traffic without inspecting mutable runtime objects."""
        nonlocal result
        if isinstance(event, OutputEvent):
            _Output(output, dropped, request, "output", written).write(event.text)
        elif isinstance(event, DiagnosticEvent):
            _Output(output, dropped, request, "diagnostic", written).write(
                render(event.diagnostic, source=event.source, color=False) + "\n"
            )
        elif isinstance(event, SessionStarted):
            events.send(WorkerEvent("loaded", event.document_id, request))
        elif isinstance(event, ResultEvent):
            result = "\n".join(value[:2048] for value in event.values[:16])

    session = SessionService(event_sink=present)
    stopping = False
    # Launchers may ignore SIGINT; the child must explicitly own its interrupt.
    signal.signal(signal.SIGINT, signal.default_int_handler)

    def interrupt() -> None:
        """Interrupt Python execution without blocking behind command output."""
        stop.wait()
        _thread.interrupt_main()

    threading.Thread(target=interrupt, daemon=True).start()
    events.send(WorkerEvent("ready"))
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
                raise ValueError("invalid session request")
            result = ""
            written[0] = 0
            if operation == "shutdown":
                break
            events.send(WorkerEvent("started", operation, request))
            old_stdin = sys.stdin
            sys.stdin = _Input(inputs, events, request)
            try:
                with (
                    redirect_stdout(
                        _Output(output, dropped, request, "output", written)
                    ),
                    redirect_stderr(
                        _Output(output, dropped, request, "diagnostic", written)
                    ),
                ):
                    if operation == "load":
                        preparation = session.prepare(snapshot=message.snapshot)
                        succeeded = preparation.successful
                        if preparation.prepared is None:
                            for diagnostic in preparation.diagnostics:
                                present(
                                    DiagnosticEvent(
                                        diagnostic, message.snapshot.root.source
                                    )
                                )
                        else:
                            events.send(WorkerEvent("prepared", request=request))
                            commit = commands.get()
                            if commit != (request, "commit", True):
                                events.send(
                                    WorkerEvent(
                                        "cancelled",
                                        "Source changed; run again",
                                        request,
                                    )
                                )
                                continue
                            try:
                                succeeded = session.execute(
                                    preparation.prepared,
                                    workspace_revision=message.snapshot.workspace_revision,
                                ).successful
                            except ValueError as exc:
                                events.send(WorkerEvent("cancelled", str(exc), request))
                                continue
                    else:
                        succeeded = session.run(source).successful
                    if not succeeded and session.loaded_source is None:
                        events.send(WorkerEvent("reset", request=request))
                events.send(
                    WorkerEvent(
                        "finished" if succeeded else "failed",
                        result,
                        request,
                        written[0],
                    )
                )
            finally:
                sys.stdin = old_stdin
    except KeyboardInterrupt:
        stopping = True
    finally:
        session.close()
        # Interrupt acknowledges a Python boundary, not complete native cleanup.
        events.send(WorkerEvent("interrupted" if stopping else "closed"))
        output.cancel_join_thread()
        events.close()


class SessionWorker:
    """Own bounded channels and a replaceable persistent session process."""

    def __init__(self) -> None:
        """Initialize transport state without starting or running any source."""
        self.context = mp.get_context("spawn")
        self.process = None
        self.busy = False
        self.ready = False
        self.request = 0
        self._reported_drops = 0
        self._received: dict[int, int] = {}
        self._completions: list[WorkerEvent] = []
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
            name="valiance-editor-session",
        )
        self.process.start()
        writer.close()

    def submit(self, source: str = "", *, snapshot=None) -> int:
        """Prepare a fresh captured document or run a persistent REPL command."""
        if not self.ready or self.busy or self.process is None:
            raise RuntimeError("worker is not ready for a submission")
        self.request += 1
        self.commands.put_nowait(
            SessionRequest(
                self.request,
                "load" if snapshot is not None else "command",
                source,
                snapshot,
            )
        )
        self.busy = True
        return self.request

    def commit(self, request: int, current: bool) -> None:
        """Acknowledge preparation only when the UI still owns that revision."""
        self.commands.put_nowait((request, "commit", current))

    def provide_input(self, text: str) -> None:
        """Supply one program-input line without turning it into a REPL command."""
        self.inputs.put_nowait(text.rstrip("\n") + "\n")

    def poll(self) -> list[WorkerEvent]:
        """Read control first and cap each output batch to preserve redraw time."""
        if self.process is None:
            return []
        result = []
        try:
            while self.events.poll():
                event = self.events.recv()
                if event.kind in {"finished", "failed"}:
                    self._completions.append(event)
                    continue
                result.append(event)
                if event.kind == "ready":
                    self.ready = True
                if event.kind in {
                    "finished",
                    "failed",
                    "cancelled",
                    "interrupted",
                    "closed",
                }:
                    self.busy = False
        except EOFError, BrokenPipeError:
            if self.ready or self.busy:
                result.append(WorkerEvent("closed", "Session worker exited"))
            self.ready = False
            self.busy = False
        for _ in range(64):
            try:
                event = self.output.get_nowait()
                self._received[event.request] = self._received.get(event.request, 0) + 1
                result.append(event)
            except queue.Empty:
                break
        pending = []
        for event in self._completions:
            if self._received.get(event.request, 0) >= event.sequence:
                result.append(event)
                self._received.pop(event.request, None)
                self.busy = False
            else:
                pending.append(event)
        self._completions = pending
        with self.dropped.get_lock():
            dropped = self.dropped.value
        if dropped > self._reported_drops:
            result.append(WorkerEvent("truncated", f"Dropped {dropped} output chunks"))
            self._reported_drops = dropped
        return result

    def close(self, *, grace: float = 0.3) -> str:
        """Try interruption, then bound exit using process termination and join.

        Forced termination forfeits language/native cleanup and always
        discards state; a stopped session must be replaced before reuse.
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
            raise RuntimeError("session worker process did not exit")
        self.events.close()
        for channel in (self.commands, self.inputs, self.output):
            channel.cancel_join_thread()
            channel.close()
        process.close()
        self.process = None
        self.ready = self.busy = False
        return disposition
