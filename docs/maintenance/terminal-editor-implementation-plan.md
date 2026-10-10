# Terminal editor implementation plan

Status: Stage 0 accepted by the user, 10 October 2026. The isolated Textual/process
harness and reusable editor widgets are implemented, including the requested
resize-discoverability adjustment. See the
[feasibility evidence](terminal-editor-feasibility.md) for measurements and
remaining validation. Stage 1 service extraction and independent state models
are implemented. Stage 2's document editor is available through the internal
launch route, including basic file runs/persistent commands pulled forward from
Stage 5 during feedback. Stage 3 is next; Stage 5 is not complete. Public CLI
switching has not started.

The [behavior specification](terminal-editor-design.md) is the product contract;
the [HTML reference](terminal-editor-reference/index.html) illustrates it. All
confirmed prototype feedback applies to the TUI. Its JavaScript semantic fixtures
must never become the compiler implementation.

## Selected TUI approach: a new Textual frontend

The user selected Textual and a fresh implementation of the terminal frontend,
based on past friction with the prompt-toolkit experience. This supersedes the
previous framework recommendation. Stage 0 validates integration details within
Textual; it is not a framework-selection contest.

Build a Python `textual.app.App` under `src/valiance/terminal_editor/`, with new
widgets, actions, message handling, and Textual CSS. Do not adapt, subclass, or
wrap the old prompt-toolkit frontend, buffers, key handlers, or rendering code.
Use the existing language compiler, module loader, diagnostics, and VM through
shared services. Starting fresh applies to the UI, not a rewrite of the language.

Textual's [TextArea documentation](https://textual.textualize.io/widgets/text_area/)
covers editing, selection, wrapping, undo/redo, themes, and custom rendering.
Use it as the editing foundation and add the specified Valiance behaviors through
a focused editor widget. Do not write a new general-purpose text editor from
scratch. Add a compatible Textual dependency during Stage 0, verify Python 3.14
and packaging, and record the tested version range.

The HTML/CSS does not run inside the terminal. Translate its layout into Textual
containers and cell dimensions, and its colors into named style roles in `.tcss`
and TextArea themes. Retain behavior and visual hierarchy rather than copying
pixel dimensions, browser typography, or wavy underline fidelity.

### Concrete rendering map

- Root vertical layout: custom file strip; main working area; REPL divider and
  pane; custom status row. Use Textual containers and CSS grid/flexible sizing.
- Main horizontal layout: source editor; custom draggable divider; inspector.
  Keep document editing state when panes hide or tabs change. Prefer one mounted
  editor per open document, shown through a content switcher, so caret, undo and
  scroll state survive without reloading text on every tab switch. Stage 0 checks
  the memory cost and retained state before finalizing that arrangement.
- Source editor: a focused TextArea extension with line numbers, soft wrap on,
  configured spaces/indentation, shared Valiance token styling and diagnostics.
  `Changed` messages update document revisions; selection/cursor changes query
  inspection. Source row/column and offsets remain independent of wrapped cells.
- Highlighting: validate TextArea's custom line-rendering extension points for
  applying compiler token spans and diagnostic emphasis. A tree-sitter grammar
  is not a prerequisite and must not become a second semantic authority. Keep
  the adapter isolated and test it against the selected Textual version.
- Inspector: small composed widgets for stack, last element, function context,
  selected element, errors, and minimized pin. A compact `Overload ‹ 1 2 3 ›`
  control appears only for multiple surviving worlds and updates these facts.
- REPL: bounded styled transcript (RichLog or a dedicated virtualized widget after
  validating retention/export/selection) and a separate TextArea command widget.
  The command widget implements Enter lock, history, and completion precedence;
  source and REPL input share editing services, not submission behavior.
- Menus and completion: compact anchored overlays. File/save/rename/reload and
  dirty-close flows use modal screens; search/replace uses an inline editor bar.
  Widget messages dispatch controller actions, not compiler operations.
- Resize messages choose side-by-side or switchable compact views. Test at 80x24,
  120x40, and 160x50; choose usable cell minima rather than copying CSS pixels.
- Mouse diagnostic clicks map rendered cells through TextArea's wrapping and
  navigation model to source ranges. Divider widgets use mouse capture and also
  expose keyboard actions. No required interaction depends on mouse support.

### Keyboard and worker integration are early gates

Textual cannot recover key distinctions that the terminal never sends. Stage 0
checks actual `Key` events for Alt+X, Ctrl+Enter, Shift+Enter, Ctrl+H, Ctrl+[, Ctrl+],
Ctrl+Shift+S, and tab navigation in target terminals. Modified Enter can collapse
to Enter, Ctrl+H can alias Backspace, and Ctrl+[ can collide with Escape. Preserve
ordinary typing; provide menu actions and configurable distinguishable fallback
bindings for unsupported chords. Verify upgraded terminal protocols where
available rather than assuming support.

Use Textual messages/reactive state to update widgets, and its
[worker facilities](https://textual.textualize.io/guide/workers/) to manage
asynchronous requests and IPC readers. UI mutation stays on the application
thread. Textual worker cancellation does not establish VM or native-call Stop
semantics; execution still uses the separately owned persistent process described
below. Test the application with Textual's
[headless testing and Pilot](https://textual.textualize.io/guide/testing/).

## Existing integration points and missing capabilities

| Area | Existing code | Work needed |
| --- | --- | --- |
| CLI entry | `main.py::_run` sends both no-argument entry points to `_run_repl`. | Route bare `vln` to the new app only after acceptance. Decide whether bare `valiance` follows the same route; preserve explicit commands and redirected/plain use. |
| Editing/frontend | `repl.py::_PromptToolkitFrontend` remains the public REPL UI; `terminal_editor/app.py` provides the new internal Textual app. | Complete later-stage input/inspection behavior without importing legacy UI helpers. Obtain tokens, candidates and documentation from shared language services. |
| Runtime session | `sessions/service.py::SessionService` owns analyser, branch, VM globals, stack and imports; `main.py::_ReplSession` adapts events to CLI output. `sessions/worker.py` hosts the editor session. | Service extraction and prepare/commit are implemented. Finish Stage 5 focus, input preferences/history, transcript controls/styling/export and native lifecycle validation. |
| Workspace | `CompilationDatabase` owns overlays/invalidation; `incremental/snapshots.py` captures untitled roots and the import closure; `terminal_editor/models.py` owns document identities/revisions. | Captured loads and overlay ownership are implemented. Add recoverable editor diagnostics and cursor inspection products in Stages 3–4. |
| Semantic presentation | `lsp.py`: hover, selected-overload docs, variable types, completion, navigation. | Share semantic queries below frontend formatting; avoid driving a local LSP transport or copying hover logic into the TUI. |
| Cursor state/worlds | Parser lowering, branch-based analysis, typed overload choices. | Preserve source boundaries, before/after facts, branch ancestry, and only end-surviving worlds. Final typed AST or prefix analysis alone is insufficient. |
| Execution lifecycle | VM execute and task/native cancellation machinery. | Prove editor-wide Stop, blocked native calls, worker cleanup, responsiveness, and persistent session ownership. Existing task cancellation is not automatically whole-session cancellation. |

Runtime REPL commands such as `:branch`/`:restore` are distinct from static
overload-world inspection. Preserve their existing semantics during extraction;
do not reuse runtime branch copying to implement the static inspector.

## Ownership and service contracts

Use `src/valiance/terminal_editor/` for frontend-specific models, actions, rendering,
key maps, file operations, and application startup. Put shared semantics and
session services in appropriate compiler/runtime-facing packages, not in widget
callbacks. Exact filenames are chosen at the relevant stage; avoid a giant
replacement `main.py` or a parallel language implementation.

| Owner | Contract |
| --- | --- |
| Document/workspace controller | Stable document ID, optional canonical disk path, launch-directory fallback, text revision, saved baseline, cursor/selection, undo, and view state. All open disk documents supply overlays. |
| Analysis service | Consumes an immutable workspace revision plus root document. Returns tokens/ranges, structured diagnostics, scope/context, and inspection indexes. It never executes code. |
| Inspection product | Source checkpoint -> surviving world IDs -> stack, variables, preceding semantic operation, selected overload and documentation. Distinguishes empty, unavailable, unreachable, and multiple states. |
| Session service | Prepares a fresh load or next command, executes it, emits typed events, owns live runtime state, and resets/stops. The UI does not inspect mutable VM objects. |
| UI controller | Applies actions and accepts matching-revision events; keeps focused document, visible inspector, loaded source, and runtime status independent. |
| Persistence/configuration | History, recent paths, styling and preferences; no source execution or automatic reopening/restoration of runtime state. |

Analysis runs outside the UI event loop with one owner of each mutable compiler
workspace. Coalesce requests, prioritize the active document, and drop obsolete
responses. Tag facts with root and dependency revisions: an unchanged root is
still stale if an open import changes. Cursor movement queries indexes rather
than triggering execution or full analysis. Clear obsolete current facts as soon
as text changes; show a neutral unavailable/pending state, with “Updating…” only
once the current request exceeds 500 ms.

Recommended runtime topology: a persistent session worker process, with dedicated
IPC separate from captured program stdout/stderr. It keeps analyser, VM, globals,
and stack together; send immutable source snapshots and presentation events,
not pickled live VM state. A separate analysis worker owns editor analysis so a
running program cannot delay cursor inspection. Stage 0 validates startup cost
and packaging on Windows/Python 3.14 before committing this topology.

Prepare a load entirely against the captured root/import snapshot, including
code generation, before committing the fresh session. Preparation failure leaves
the old session intact. Revalidate snapshot identity; never execute an older
successful compile after the current revision fails. A successful commit emits
the session separator and focuses/reveals REPL. Edits after execution begins do
not change its source; loaded-source labels retain the executed revision.

Stop first requests cooperative cancellation/cleanup. A persistent worker
provides a bounded escape from unresponsive execution, but force termination
requires an explicit lifecycle policy for native resources and child processes.
Do not claim stopping a Python thread stops native code. After Stop/runtime fault,
reset runtime state and preserve transcript/history/lock as agreed. Reset itself
also clears transcript; Clear only clears transcript. Bound output queues as
well as retained transcript, and keep Stop/control events from waiting behind
output floods. Export only retained transcript and its truncation marker.

## Staged delivery

Each stage has a reviewable deliverable and an exit gate. Complete its defined
behavior and tests before starting dependent work. Internal stages need not
become the public default. Existing CLI behavior remains available until Stage 8;
no fake stack facts or temporary evaluator enter production.

### Stage 0 — prove terminal and worker feasibility

Deliver an isolated feasibility harness under the appropriate development tools
subdirectory, with a documented result matrix, not a replacement CLI. Exercise
editable source + transcript + inspector at real terminal sizes; wrapped cursor
and mouse mapping; diagnostic overlays; divider dragging; focus; clipboard;
modified keys; and redraw during a busy worker. Test Windows Terminal/PowerShell
first, then a Unix terminal and SSH path for portability. Check the selected Textual version on Python 3.14, not only web examples.
Prove token-span styling, diagnostic overlays, document undo retention, and
wrapped mouse/caret mapping using supported TextArea extension points.

Exercise a persistent worker with a real VM program, repeated REPL submissions,
large output, Stop, and blocked native execution. Measure latency and cleanup.
Resolve worker startup/packaging, bytecode/runtime snapshot preparation, input
routing if a program reads input, and graceful/forced stop policy.

**Exit:** validated Textual dependency/version, measured limitations, supported
key map, TextArea extension design, worker protocol/lifecycle, and terminal
sizing rules. Keep useful harness widgets as the foundation of the new app;
resolve unsupported interactions explicitly before dependent work.

### Stage 1 — extract services and establish state models

Extract session mechanics from `_ReplSession` without changing language behavior;
use structured diagnostics/output/results instead of service-level printing.
Separate prepare/execute so compile failures do not mutate the committed session.
Model documents, workspace revisions, loaded source, runtime status, transcript,
lock/history, and inspector selection. Define worker request/event types and
shutdown. Integrate CompilationDatabase overlays and module loader ownership;
represent untitled source without creating a fake saved file.

**Exit:** existing REPL persistence/import/branch-command tests still pass;
preparation failure preserves state; clear/reset/load behavior is testable without
a terminal. Document and session state cannot accidentally change one another.

Stage 1 implementation (10 October 2026):

- `sessions/service.py` owns persistent analysis, loader, VM, stack and runtime
  branches. `_ReplSession` is now a CLI presentation adapter. Preparation works
  on isolated compiler state; failed or superseded preparations never commit.
  Fresh document loads use the compiler's complete module declaration phases.
- `sessions/events.py` defines immutable presentation and worker lifecycle
  records. Both CLI exit and probe shutdown close all owned session executors.
  The process probe consumes the shared service rather than reading its VM.
  Streamed output and retained responses are separate; retained output is bounded
  and indicates truncation.
- `incremental/snapshots.py` captures root identity, all open overlays and the
  resolved transitive source/artifact closure, including absent candidates and
  project metadata. Frozen loaders read captured bytes. Document/dependency edits,
  changed disk files or newly appearing candidates invalidate the load gate.
  Untitled roots have no fabricated path and resolve imports from their launch
  directory. Existing compiled interfaces are checked against overlay bytes.
- `terminal_editor/models.py` keeps documents, saved baselines, view coordinates,
  workspace revisions, loaded revision, runtime status, transcript, input lock,
  bounded history/draft recall and inspector selection independent. Clear only
  clears transcript; Reset clears session and transcript; a failed Load retains
  the previous session. Mounted TextArea widgets retain editing undo state.
- Regression coverage is in existing `test_main.py`,
  `test_compilation_database.py` and `test_terminal_editor.py`. Frontend wiring,
  file dialogs and asynchronous production worker control remain in their
  respective later stages.

Stage 1 validation: the focused CLI/session, compilation database, terminal
editor, fundamental programs and docstring run passed 172 tests. Additional
transaction/snapshot and worker/output checks passed, including the final
transcript retention change. Ruff check/format passes for the new service,
snapshot/model files and worker adapter. The full suite ran 2,146 tests:
2,135 passed and the same 11 existing native DLL-loading errors recorded in
Stage 0 remain; the error-name set matches that baseline. `test_programs.py`
was not modified. Stage 1's exit gate is satisfied with that existing native
validation limitation carried forward.

### Stage 2 — full-screen editor and document lifecycle

Build the new Textual App, real document editors, and four-pane shell with `.tcss`. Implement new/open/recent,
save/save-as, rename, close/quit prompts, duplicate-path identity, tabs and focus,
selection/copy/cut/paste, undo/redo, search/replace, visual wrapping, indentation,
status, pane visibility and size restoration. Provide an internal launch route
for evaluation; do not switch bare `vln` yet. Connect overlays on open/edit/save/
rename/close. Detect external changes and always offer Reload/Keep.

**Exit:** keyboard-only editing and file workflows pass, failed/cancelled saves
keep documents open, last-close creates untitled, rename preserves unsaved text,
and relative imports have the correct path/launch base. Wrapped Unicode/tabs and
mouse selection retain correct source offsets at the target sizes. No runtime
execution or invented inspection data is necessary for this stage.

Stage 2 implementation (10 October 2026):

- Internal evaluation: `uv run python -m valiance.terminal_editor`. The new app
  starts with one blank numbered untitled document. Bare `vln`/`valiance` still
  use their existing frontend. Execution was connected during Stage 2 feedback:
  F5/Alt+X and Run prepare the active buffer and its captured imports, then load
  a fresh persistent session in a spawned worker. Compilation failure preserves
  the prior session; changed preparations cannot commit. Live semantic inspection
  remains Stage 3 work.
- File menu (F2) and shortcuts provide New, Open, Open Recent, Save, Save As,
  Rename, Reload, Close and Quit. Path dialogs support keyboard entry and a
  directory browser; relative paths use the active file's directory or launch
  directory. Files are UTF-8 and preserve existing newline conventions. Save As
  establishes its new import base without deleting the original saved file.
- Atomic save transactions capture text before worker I/O, then commit overlays
  on the UI thread. Failed/cancelled saves preserve documents. New edits during
  a save remain dirty and prevent closing. Rename moves saved bytes while
  retaining unsaved text and the native widget's caret, selection and undo.
  Existing destinations require explicit replacement; paths open in another tab
  cannot be overwritten. Canonical duplicates activate retained editors, and
  matching basenames receive distinguishing path labels.
- Close/Quit protect each dirty document. Last-close creates a blank untitled
  document. Explicit reload protects dirty text. Coalesced disk polling always
  offers Reload/Keep, including clean buffers and deletion; Keep retains editor
  text as unsaved when different. Dismissing a prompt defers that disk revision
  without silently acknowledging it for a subsequent save.
- Each document remains mounted in a ContentSwitcher. Native clipboard,
  selection, undo/redo and visual wrapping are retained. Literal, case-sensitive
  search/replace is scoped to the active document; replacement and block indent
  actions are undoable. Automatic indentation reuses the source formatter and
  lexer, including immediate closing `end` dedent outside strings/comments.
- F6 cycles panes; F3/F4 show State/REPL. The View menu provides wrapping,
  REPL-only and pane visibility; a separate Edit menu provides editing actions
  and indentation width. Compact dropdowns close via Escape, Close menu or
  clicking outside. Each dropdown anchors beneath its own single-line toolbar
  control without dimming or moving the underlying panes. Buttons and tabs share
  an inset label row above the tab underline. Compose assigns the menu anchor
  before its first frame; layout constrains it to terminal edges, avoiding an
  opening-position flicker. Dividers retain mouse/keyboard
  resizing and requested dimensions through hide/show. At 80x24 panes become
  switchable views with editor priority; 120x40 and 160x50 show the wider shell.
  Layout uses resize-event dimensions rather than the previous screen size.
- Safe fallback keys: F7 opens Replace, F10/Shift+F10 indent/dedent. Ctrl+H can
  alias Backspace and Ctrl+[ can alias Escape, so normal typing is preserved;
  distinguishable Ctrl+[/Ctrl+] events still reach the editor indent actions.
  Ctrl+Shift+S also has a Save As menu fallback. Physical key/OS-clipboard
  compatibility remains tracked by the Stage 0 terminal gates.
- REPL Enter/Ctrl+Enter submits; Shift+Enter inserts a newline. Enter lock is not
  wired into this app yet; its eventual modified-Enter bindings remain Stage 5
  work. Commands continue the loaded
  session; requested program stdin uses the same input box via a separate channel.
  F8/Stop interrupts and reaps the worker off the UI loop, resets the session and
  retains documents/transcript. Worker output and transcript retention are bounded.
  Exit reaps the process. Native cleanup/terminal protocol proof remains a later gate.
- Recent paths, wrapping, indentation width and pane sizes are bounded and stored
  in `valiance/terminal-editor.json` under LOCALAPPDATA/XDG_CONFIG_HOME (or
  `~/.config`). Startup does not reopen paths or restore source/runtime state.
  All new coverage is in the existing `tests/test_terminal_editor.py`.

Stage 2 feedback validation: the six focused UI/process/docstring checks pass,
including both run shortcuts, persistent REPL commands, compile-failure rollback,
program stdin, responsive Stop/exit, stale-load rejection, output-before-completion
ordering, toolbar alignment and mouse menu dismissal. Fundamental programs also
pass unchanged. The screenshot's range/foreach program runs through the fresh-load
path and prints 1 through 100. Physical terminal key delivery remains a user-review
gate.

Stage 2 validation: 192 focused terminal-editor, CLI/session, compilation
workspace, fundamental program and production-docstring checks passed. This
includes real keyboard/modal workflows, cancelled/failed saves, multi-document
quit, edits during a slow save, external Reload/Keep, path-label disambiguation,
recent files, Unicode clipboard/search, wrapping, automatic/block indentation,
and pane layouts at 80x24, 120x40 and 160x50. Ruff check/format and the offline
wheel build pass; the wheel includes the app, dialog/editing/file modules, internal
entry point and `.tcss` asset. The final coalesced watcher check and clean-quit
shell smoke check also pass. Fundamental tests were not modified. The whole
compiler suite was not repeated after Stage 2; Stage 1 records 11 native
DLL-loading errors as a separate validation limitation. Basic execution was
brought forward during feedback rather than declaring Stage 5 complete.

Toolbar follow-up validation: 49 checks passed for menu anchoring, transparent
backdrop, unchanged pane layout, narrow resize, fundamental programs and
docstring coverage. The subsequent alignment/first-frame fix passed 48 checks,
including observing menu placement before deferred callbacks. These are separate
focused runs, not cumulative full-suite counts. Ruff checks pass; physical
terminal rendering remains subject to user review.

### Stage 3 — live compiler diagnostics and basic inspection

Connect real analysis snapshots and recoverable lexer/parser diagnostics. Share
selected-overload documentation and semantic navigation with the LSP. Add source
hit rules, diagnostic click/navigation, single-state stack checkpoints, function
context, last element including variable types, and static chain boundaries.
Preserve lexical/AST source spans through lowering as needed. Gather all
recoverable diagnostics across the import closure; valid independent regions
remain inspectable when another region fails.

**Exit:** `println perimeter $circle` shows Circle at the perimeter/circle gap
and Real at the println/perimeter gap; token hits show overloads rather than
stack. Source changes invalidate facts across dependent files; delayed workers
cannot restore stale facts. Inspector never executes code. Codegen preparation
errors are included in the load gate, not discovered after session reset.

### Stage 4 — surviving overload-world traces

Add optional analyser trace collection with stable semantic operation/boundary
IDs, branch ancestry and immutable checkpoint facts. After full function/return
validation, index only surviving worlds. Include generic substitutions, locals,
nested scopes and control-flow states; merged branches must not discard facts
needed for cursor inspection. Keep distinct control-flow alternatives correctly
labelled rather than pretending each is an overload.

Connect the subtle numbered page control, synchronized selection throughout a
function, world-specific resolved overloads/docs, and silent fallback if a
selected world disappears. Use structural identities within a revision and
explicit correspondence across edits where possible, never page ordinal alone.

**Exit:** real programs produce multiple inspectable worlds; a locally viable
world rejected later is absent at earlier cursor points. No survivors yields the
existing compiler diagnostic. Turning tracing off does not change compilation
results; benchmark memory/time and bound retained traces without inventing facts.
This gate must pass before describing the State pane as complete.

### Stage 5 — persistent REPL, Alt+X, and execution lifecycle

Basic worker/session integration, fresh loads, persistent commands, program stdin,
bounded output, stale-load rejection and bounded Stop/exit are already implemented
in Stage 2. Do not replace those paths with another execution implementation.
Finish automatic focus transfer/REPL reveal, including compact views; visible
compile-blocked-load indication; semantic command/output/result styling;
transcript selection/export and Clear/Reset controls. Complete history
persistence/draft restoration, Enter lock with both distinguishable modified-Enter
shortcuts, and contextual input rules. Validate the independent state transitions
for load, reset, fault and Stop, and the real Valiance FFI/native/child-resource
cleanup policy. Continue testing edits during execution and rejection of repeated
loads against the existing worker; do not queue another run.

**Exit:** definitions and top-level stack/variables from unsaved files are usable
in later commands; switching tabs changes neither runtime nor loaded source.
Invalid load leaves the prior session intact. Infinite loops/output floods and
native-call cancellation meet the Stage 0 lifecycle contract while the editor
remains usable. Clear, Reset, Stop, successful load, and fault retain/clear the
correct independent states. Result green never colors printed program output.

### Stage 6 — complete diagnostic interactions and completion

Finish error-list visibility only when errors exist, grouped import diagnostics,
expanded detail with source emphasis, immediate brightness restoration,
minimized pin, and pin dismissal on every edit/undo/redo. New errors keep cursor
inspection active; attempted invalid loads reveal diagnostics without stealing
editor focus. Keep warnings/lints secondary.

Provide completion from cursor-world stack context in source and from the current
session plus draft in REPL. Group overloads per name; automatic popup for 1–8
candidates, explicit Ctrl+Space for larger sets, correct Tab/Escape/Up/Down/Enter
priority, and undo grouping with edits. Do not derive candidate applicability
from name lists alone.

**Exit:** selected-error and completion lifecycles pass in wrapped source,
imports, incomplete edits, multiple worlds, REPL history, and locked input.

### Stage 7 — terminal styling and compatibility hardening

Complete named themes and independently configurable roles, default screenshot
appearance, color-depth fallback, keyboard access for every interaction, tab
label disambiguation/overflow, deep-stack and transcript scrolling, compact
layouts, progress timing, and configuration validation. Test terminal lifecycle
restoration on quit/fault, clipboard facilities, SSH/no-mouse operation, and
external reload during both analysis and execution. Preserve within-session
pane sizes; decide separately whether size preferences persist across launches.

**Exit:** all nine reference scenarios and feedback work with real compiler data
at target sizes, including light/low-color modes. Usability review validates
subtle overload controls and unobtrusive warnings. No required behavior depends
on browser APIs or a mouse.

### Stage 8 — switch the entry point and retire old scratch mode

Route no-argument `vln` on a capable terminal to the completed editor, starting
with blank untitled/all panes visible. Preserve plain/non-TTY behavior and
explicit CLI commands; finalize the bare `valiance` alias policy and help text.
Retire superseded prompt-toolkit scratch/frontend code once callers/tests move
to shared services. Audit remaining prompt-toolkit consumers (including other
CLI menus) before removing its dependency; the new editor has no dependency on
it. Preserve or separately migrate plain-input and unrelated CLI behavior. Update installation/use docs and this plan with
completed stage evidence.

**Exit:** complete regression suite and fundamental programs pass, packaged
entry points work on supported platforms, no legacy scratch path is necessary
to satisfy a new-editor feature, and terminal state is restored on all exits.

## Test and review strategy

- Service/model tests validate transitions without terminal timing dependence.
- Parser/analyser tests verify source mapping, end-surviving worlds, and unchanged
  language semantics; database tests verify overlays/dependency invalidation.
- Runtime/CLI tests verify persistent commands, prepare/commit safety, fresh load,
  import preludes, worker termination, and explicit-command compatibility.
- Drive the Textual App with `App.run_test()` and Pilot key/click/resize actions.
  Assert widgets, document state, focus, and messages rather than old prompt
  rendering details. Use controlled worker completions for stale-result and
  500 ms tests. Pair automated tests with real terminal sessions for actual
  mouse/wrap/color/key delivery. Add visual snapshots where stable and useful;
  interaction/state assertions remain the primary behavior checks.
- Add tests to appropriate existing `tests/test_main.py`, `test_lsp.py`,
  `test_compilation_database.py`, parser/analysis/runtime suites. A genuinely new
  full-screen application test suite is justified by its subsystem scope; place
  it under tests and link it from the maintenance guide, not a patch-specific file.
- Keep `tests/test_programs.py` unchanged and passing at every production stage.
  Run focused tests, fundamental programs, and the complete suite as required by
  repository maintenance guidance. Documentation-only planning does not require
  a runtime regression run.

## Decisions to close before their stage starts

1. Stage 0: actual modified-key support/fallbacks, process packaging and stop
   semantics, execution stdin routing, and measured pane minima.
2. Stage 1: untitled identity/API, immutable dependency closure capture, and
   transactional REPL compile failure behavior.
3. Stage 3/4: remaining comment/declaration/block-edge hit rules, unreachable and
   branch-state labels, world identity across edits, and trace cost limits.
4. Stage 7/8: configuration/history retention limits, cross-launch pane-size
   preferences, alias/non-TTY behavior, and exact supported terminal matrix.

These are explicit implementation decisions with owners and gates, not reasons
to reopen already confirmed interactions. Stage 0 was accepted after user review;
Stages 1–2 are implemented and Stage 3 live diagnostics/basic inspection is next.
The Stage 5 work brought forward does not close its remaining input, presentation
and native-lifecycle gates.
