# Terminal editor feasibility evidence

Stage 0 accepted by the user on 10 October 2026 after testing the harness's
basics and reviewing its terminal appearance. Resize discoverability was the
remaining requested adjustment: dividers now have visible directional handles,
a “Drag to resize” label, hover feedback and a keyboard-help tooltip.
This report records the Stage 0 harness and its historical measurements.
Stages 1–2 now provide the shared session service and internal editor, with basic
execution brought forward from Stage 5. See the
[current terminal behavior](terminal-editor-design.md#current-terminal-implementation)
and [implementation plan](terminal-editor-implementation-plan.md) for current
shortcuts and validation. Probe key bindings and measurements below are not the
production editor's bindings or current performance measurements.

## Running the probes

From the checkout in PowerShell:

```powershell
$env:UV_CACHE_DIR="$PWD\.uv-cache"
uv run python -m tools.terminal_editor
uv run python -m tools.terminal_editor.measure
uv run python -m unittest tests.test_terminal_editor -v
```

The [development app](../../tools/terminal_editor/app.py) mounts two independent
documents, a static inspector, bounded transcript, command input and draggable
dividers. It uses the [source widget](../../src/valiance/terminal_editor/editor.py)
and [divider widget](../../src/valiance/terminal_editor/divider.py) as foundations
for the new frontend. The public `vln` and `valiance` entry points retain
their existing behavior.

The transcript and command editor have separate titled borders, “REPL output”
and “REPL input”. Input has a contrasting background, a highlighted focus border,
a typing placeholder and an adjacent Enter/multiline hint.

Run submits source to the existing persistent REPL session in a spawned process;
it is explicitly a session feasibility probe, **not fresh-session Alt+X loading**.
It does not save files. The inspector says “State unavailable” and reports actual
source coordinates; it never presents fixture stack facts. Lexical diagnostic
emphasis uses the compiler's recoverable lexer. Type diagnostics, selected-error
interactions and cursor checkpoints belong to later stages.

The tool intentionally invokes existing session mechanics rather than duplicating
the compiler pipeline. Its legacy stdout presentation is neutral, including the
legacy implicit stack print. A separate runtime-result event is green. Extracting
structured diagnostics, printed output and results without duplicate legacy
presentation remains Stage 1 work. A failed probe submission does not provide
the transactional preparation guarantee required for production loads.

## Automated evidence

Tested dependency: **Textual 6.12.0**, CPython **3.14.5**, Windows 11 build 26200.
The declared range is `textual>=6.12.0,<7`; only 6.12.0 has been exercised.
No tree-sitter dependency or separate Valiance grammar is required.
The wheel builds successfully and includes all three `valiance.terminal_editor`
modules and the Textual requirement. Development tools are checkout-only;
installed application startup/worker packaging must be rechecked when the
production application and services exist.

[Tests](../../tests/test_terminal_editor.py) cover these behaviors:

| Capability | Observed result | Remaining validation |
| --- | --- | --- |
| Compiler-token styling | `TextArea.get_line` receives Rich spans in source character offsets before wrapping and tab expansion. Raw Unicode spelling and string delimiters retain their widths. | Full semantic roles and named themes. |
| Diagnostic emphasis | Source-offset overlays render; edits discard obsolete emphasis. Real lexical errors supply harness markers. | Selected diagnostics, keyboard/mouse navigation and imported diagnostics. |
| Retained documents | Switching mounted editors preserves text, caret, selection and undo; hiding inspector preserves chosen width. | Many-document memory measurements and disk lifecycle. |
| Wrapping and mouse | Pilot click at a wrapped caret maps back to the original source column with wide characters, combining characters and a tab. | Real mouse delivery, drag selection and terminal-specific width behavior. |
| Clipboard | Native TextArea copy/paste preserves selected source through Textual's application clipboard. | OS clipboard/OSC 52 delivery in actual terminals. |
| Enter lock | Delivered Ctrl+Enter and Shift+Enter toggle without editing/submitting. Plain Enter inserts a newline while locked. Pasted newlines remain text. | Physical modified-key delivery and history/completion stages. |
| Layout | 80x24, 120x40 and 160x50 retain mounted editors and usable source space. Below 100 columns, F3 switches source/inspector with source priority. | Real terminal redraw and final pane minima. |
| Dividers | Mouse capture messages and keyboard arrow actions use cell deltas; keyboard resizing is clamped and retained across visibility changes. | Physical mouse drag delivery. |
| Persistent process | Real VM submissions retain stack, variables and definitions across commands. | Extracted transactional preparation, overlays and import snapshot ownership. |
| Input | The real `input` builtin reads a dedicated input channel; it is never treated as another REPL command. | Production input focus/cancellation behavior. |
| Output saturation | A bounded output channel drops chunks while control completion remains readable; truncation is exposed. | Retained transcript selection/export and semantic output events. |
| UI responsiveness | Pilot edits the source while the session process is inside a blocking native call. Stop joins run off the UI thread. | Real terminal response and native-resource guarantees. |
| Stop | Infinite VM execution and a blocking C call can be terminated and reaped within the probe's bound. | Reliable cooperative VM cleanup and native/child-process lifecycle. |

The native-block probe invokes an actual C `Sleep`/`sleep` via ctypes in the
session process. It proves the process escape mechanism, not cancellation of
a Valiance FFI call or cleanup of foreign resources. Do not conflate that result
with a complete editor-wide Stop implementation.

## Measurements

One local run, with other regression tests running concurrently:

| Measurement | Value |
| --- | ---: |
| Spawn to session-ready | 2880 ms |
| Median persistent submission, five commands | 12.44 ms |
| Output chunks dropped during a 400,000-character print | 68 |
| Infinite VM Stop, including process join | 323 ms, forced termination |
| Blocking C-call Stop, including process join | 327 ms, forced termination |
| Mounted two-document app, one document with 1,000 source lines | 5.20 MiB current / 6.24 MiB peak Python allocations |

These are feasibility measurements, not performance thresholds. Memory includes
the app, widgets, lexical styles and headless driver; it is not per-document
memory or total process RSS. Re-run the measurement module on the target machine
when evaluating responsiveness and startup. Both Stop measurements used the
forced path: the probe's Python interrupt request did not establish a reliable
cooperative cleanup boundary within 300 ms.

## Regression verification

- All 12 terminal editor/worker tests pass, including a busy-worker quit and
  mouse divider dragging. Both modified-Enter chords and the fallback key are
  recorded before app/widget key handling.
- The fundamental program suite and production docstring check pass (47 checks
  together). `tests/test_programs.py` is unchanged.
- Ruff checks and formatting checks pass for the new subsystem and its tests.
- Wheel build and inspection pass.
- Full discovery ran 2,125 tests: 2,114 passed and 11 failed to load generated
  native fixture DLLs. Rerunning the affected bytecode/FFI concurrency suites
  independently reproduced those 11 errors in 76 tests. The selected C compiler
  is `C:/cygwin64/bin/gcc.exe`; adding its runtime DLL directory was not accepted
  as a workaround because the native test process stalled. No compiler, runtime,
  FFI fixture or test expectation was changed to mask these errors. The complete
  regression suite is therefore not recorded as passing on this machine.

## Worker and transcript decisions

The [worker probe](../../tools/terminal_editor/worker.py) uses Windows-compatible
`spawn`. One process owns the existing analyser, VM, globals and stack. Requests
and presentation events contain source strings, IDs and formatted values; live
VM objects never cross IPC. Startup is asynchronous from the UI's perspective.
Busy requests are rejected rather than queued.

Control uses its own pipe, separate from stdout/stderr. Output uses a 128-entry
queue and chunks of at most 2,048 characters, with nonblocking drops and a shared
counter. UI polling drains control first and at most 64 output chunks per tick.
The transcript and its source-text retention are bounded to 300 entries/lines
with an explicit dropped-output indicator. A command can supply a program input
line through a separate queue while execution is waiting.

Stop requests a Python interrupt, waits 300 ms, then terminates the process,
joins for up to one second, and uses kill plus a final one-second join if needed.
Stop/unmount ownership is serialized to prevent competing process joins. A new
worker starts with fresh runtime state; document widgets and the Enter lock stay
intact. Forced termination cannot promise Valiance destructors, foreign cleanup,
or descendant-process reaping. The probe must not be used to assess programs
that own external processes or important native resources.

RichLog works for bounded styled output and redraw, but transcript selection and
export are not yet accepted. Choose a transcript model with retained structured
entries before production export; do not treat RichLog's rendered lines as the
semantic source of truth.

## Physical terminal checklist and key fallbacks

F2 switches documents, F3 shows/hides State (switches views when narrow), F4
toggles Enter lock, F5 runs a feasibility submission, F6 cycles focus, F8 stops,
and Ctrl+Q quits. Buttons expose document, State, Run, Stop and Lock actions.
Focus a divider and use arrows to resize. The source key display records the
chords actually delivered to Textual without consuming ordinary printable input.

Record Windows Terminal/PowerShell first, then a Unix terminal and an SSH path.
For each, enter ordinary text, paste multiline text, copy a Unicode selection,
click a wrapped row, drag each divider, cycle focus, resize through all three
target sizes, and edit during a busy session. Record actual delivery of Alt+X,
Ctrl+Enter, Shift+Enter, Ctrl+H, Ctrl+[, Ctrl+], Ctrl+Shift+S, Ctrl+PageUp and
Ctrl+PageDown. Pilot's injected key names prove application handling only.
Never map an aliased Backspace/Escape to a destructive action just because a
terminal cannot distinguish Ctrl+H/Ctrl+[. Unsupported chords require menu
actions and configurable distinguishable bindings in the production key map.

| Terminal path | Keys, mouse, OS clipboard, color/redraw | Status |
| --- | --- | --- |
| Windows Terminal + PowerShell | Not exercised by an actual terminal session | Pending |
| Unix local terminal | Not available in this checkout session | Pending |
| SSH terminal path | Not available in this checkout session | Pending |

Stage 0 is **accepted for progression to Stage 1**. The user's acceptance does
not establish unmeasured terminal-protocol or cleanup guarantees. Track detailed
physical-terminal coverage, production cooperative/forced cleanup (including
Valiance FFI and children), retained-transcript selection/export, and production
worker packaging through their relevant stages before public-default acceptance.
