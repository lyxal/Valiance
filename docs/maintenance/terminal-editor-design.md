# Terminal editor and REPL design

Status: interaction design accepted on 9 October 2026. Stages 0–2 are
implemented through the internal Python launch route, with basic file execution
and persistent REPL integration brought forward from Stage 5 during review.
Stage 3 live diagnostics/inspection is next; bare `vln` still uses the existing
REPL. Follow the [staged plan](terminal-editor-implementation-plan.md) for
implementation progress and validation evidence.

## Current terminal implementation

Run `uv run python -m valiance.terminal_editor` from the checkout. Startup opens
one blank numbered untitled document; preferences do not reopen files or run
source. The Python app supports retained tabs, protected file workflows, external
Reload/Keep prompts, syntax highlighting, wrapping, search/replace, indentation,
resizable panes, fresh file runs and persistent REPL commands.

The toolbar uses compact File, Edit, View, Run and Stop controls. Labels and tabs
share an inset row; a separate underline marks the active tab. File/Edit/View
dropdowns appear beneath their own buttons on their first frame, stay within
terminal edges and do not dim or shift the editor. Escape, Close menu or a click
outside dismisses them. View contains pane/wrapping actions; Edit contains text
and indentation actions. Other file-protection/path dialogs retain their modal
presentation.

| Current shortcut | Behavior |
| --- | --- |
| F2 | File menu |
| Ctrl+PageUp / Ctrl+PageDown | Previous / next document |
| F3 / F4 | Show or hide State / REPL |
| F6 | Cycle visible panes |
| Ctrl+F / F7 | Find / Replace |
| F10 / Shift+F10 | Indent / dedent; avoids terminal aliases for Ctrl+[ |
| F5 / Alt+X / Run button | Run the active buffer, including unsaved text, in a fresh session |
| F8 / Stop button | Stop and reap the worker; discard runtime state and retain documents/transcript |
| REPL Enter / Ctrl+Enter | Submit a command, or provide requested program input |
| REPL Shift+Enter | Insert a newline |

File shortcuts below are already implemented. Fresh runs use open-file overlays
and captured disk imports. Preparation failure or a stale preparation preserves
the previous committed session. Editing or switching tabs does not change that
session. Printed output, results and diagnostics appear in the bounded transcript;
the status identifies the loaded root revision independently of the active tab.
Run reveals the REPL in the wide layout; automatic focus transfer/compact-view
reveal remains Stage 5 work. Program input focuses the command box.

The inspector currently shows “State unavailable”; live error counts, cursor
checkpoints, diagnostic navigation and completion are not implemented. REPL Enter
lock, history browsing/persistence, Clear/Reset controls, transcript selection/export
and result-specific colors also remain later-stage work. The Enter-lock shortcuts
in the target contract below therefore differ from the current interim input
bindings. Physical terminal key delivery and native cleanup remain validation
gates; the headless tests are not evidence that every terminal reports the same
modified keys.

## Browser design reference

The [interactive design reference](terminal-editor-reference/index.html) uses
[CSS styling](terminal-editor-reference/styles.css) and
[design-only interactions](terminal-editor-reference/reference.js). Open the HTML
file in a browser. The editor uses a native textarea with highlighted source,
real caret/selection tracking, wrapping, gutter alignment, separate tab buffers,
search/replace, undo, and completion. The inspector follows the caret without
scenario buttons. The nine scenario presets, theme controls, and long-run/fault
fixtures sit in a collapsible Design lab outside the terminal interface.

This is a UI simulation, not a compiler integration. Semantic states and
recognized errors come from labelled fixtures, and the REPL supports a small
set of demo commands (Circle construction, radius, numbers, and simple stack
operations). Other input is recorded without evaluation. Editing no longer
blocks loading merely because a buffer is dirty; known fixture errors block it.
Browser Open uses a file picker; Save downloads a copy and does not overwrite
local files. Quit is simulated. These limitations do not change the specified
TUI behavior. The reference remains a simulation even where the Python terminal
app now performs real file watching, import resolution and execution.

The Shapes fixture uses source-mapped execution checkpoints, keeping the stack
empty after assignment/printing, pushing Circle on the standalone variable
line, and retaining Real above Circle after the final pipeline. Intermediate
literal pushes and operations have distinct states, and trailing blank lines
retain the completed program state. The top-level effects were checked against
the compiler using a local PI definition in place of the screenshot's unavailable
import. These checkpoints remain prototype data rather than a general analyser.

## Confirmed target requirements and decisions

These describe the completed product. Use the current-implementation section
above and the staged plan to distinguish shipped behavior from remaining work.

- Running `vln` without arguments launches a single terminal editor experience.
- Startup opens a blank untitled document rather than restoring previously open
  files. The default layout shows all four sections.
- Four sections: file tabs/menu, source editor, REPL, and editor state inspector.
- Default layout displays all four sections, with multiple open files supported.
- The active file tab is darker than inactive tabs.
- File menu includes New, Save, Open, Open Recent, Save As, and Quit.
- File shortcuts: Ctrl+S saves, Ctrl+Shift+S opens Save As, Ctrl+O opens a file,
  Ctrl+N creates an untitled tab, Ctrl+W closes the active tab, and Ctrl+Q quits.
  These actions also remain in the menu.
- Closing the last tab creates a new blank untitled document.
- Opening an already-open file activates its existing tab. Files with identical
  basenames in different directories receive distinguishing path labels.
- External file changes always offer Reload / Keep editor text; never reload
  automatically, even when the document has no unsaved edits.
- Ctrl+F searches the active file; Ctrl+H opens replace.
- Ctrl+PageUp / Ctrl+PageDown switch file tabs.
- A status row shows cursor line/column, unsaved state, error count, and the file
  that initialized the current REPL session.
- Editor supports syntax highlighting and diagnostic highlighting.
- Completion suggestions appear automatically when the matching candidate set
  contains 1–8 items, based on type-stack context at the cursor. Larger sets
  do not automatically open a suggestion list.
- While completion is visible, Tab accepts the selected suggestion and Escape
  dismisses it. Completion does not intercept Enter: it remains a newline in
  the editor and follows Enter lock in REPL input.
- REPL input uses the same completion threshold and keys, with suggestions
  based on its current stack, definitions, and the command being edited.
- Multiple overloads of the same element count as one completion suggestion,
  with overload details grouped under that entry.
- Ctrl+Space explicitly opens completion, including sets larger than eight
  suggestions. The list scrolls and filters as the user types.
- While completion is open, Up/Down select suggestions instead of moving the
  cursor or browsing REPL history. Escape dismisses completion and restores
  normal key behavior, including Enter lock's history restrictions.
- Without completion open, Tab inserts indentation in the source editor and
  locked REPL input. F6 cycles keyboard focus between visible panes.
- Indentation defaults to two spaces, with configurable width. Ctrl+[ dedents
  and Ctrl+] indents.
- Automatic indentation is syntax-aware in the editor and locked REPL input:
  carry forward indentation where relevant, and dedent closing constructs such
  as `end` to match their block.
- Dedent immediately when `end` is completed as a closing keyword, without
  waiting for Enter. Do not interpret occurrences in strings/comments or a
  longer identifier as closing keywords.
- Source lines wrap visually by default. A toggle disables wrapping and enables
  horizontal scrolling. Visual wrapping does not insert source newlines.
- Alt+X runs the **whole active file**, including top-level statements, in a
  **fresh REPL session**. Subsequent commands continue from the resulting state.
- Compile errors prevent Alt+X from starting execution or resetting the REPL.
  This includes lexical, parse, type, and code-generation errors.
- Alt+X runs current editor text, including unsaved changes, without saving.
  Saving is a separate action and does not load or execute code.
- A successful Alt+X moves keyboard focus to the REPL. Program output appears
  in the REPL. Reveal the REPL if it was hidden so the output and command input
  are accessible.
- Loading preserves the REPL transcript. Each new session is marked with a
  separator, distinguishing earlier output from the newly loaded program and
  subsequent commands. Transcript retention does not preserve runtime state.
- Imports use current editor text for open files, including unsaved changes;
  files that are not open use their saved disk source. Loading does not save
  any of these files.
- Relative imports resolve from the directory of the file containing the
  import. Documents without a file path (including the initial blank untitled
  document) use the directory where `vln` was launched. Opening an existing
  file establishes its directory immediately; Save As establishes the new base.
- Closing a document with unsaved changes prompts Save, Discard, or Cancel.
  Quit applies this protection to every unsaved document. Untitled documents
  use Save As; cancelled or failed saves leave the document open.
- REPL commands preserve stack, variables, definitions, and imports between
  submissions. They can use definitions established by the loaded file.
- Switching file tabs does not change or reload the REPL session. The inspector
  follows the active file; Alt+X explicitly initializes a fresh REPL session
  from that file.
- REPL input has an Enter lock toggle: when enabled, Enter inserts newlines;
  when disabled, Enter submits. Toggling the lock does not submit or alter text.
- Ctrl+Enter and Shift+Enter toggle Enter lock, with a visible indicator.
- REPL input starts unlocked: Enter submits by default.
- Submitting a REPL command preserves the Enter lock setting; changing it
  requires an explicit toggle.
- Alt+X and REPL reset also preserve Enter lock. It is an input preference,
  separate from runtime state; a new application launch starts unlocked.
- In REPL input, Up/Down move within the current command. When unlocked, Up
  accesses history at the first line and Down at the last line. When Enter lock
  is on, both keys stay within the command and never navigate history.
- History browsing preserves the unfinished draft and restores it when the
  user returns past the newest history entry.
- REPL command history persists across application launches. History retention
  does not restore runtime state, reopen documents, or execute previous commands.
- Clear removes the visible REPL transcript without changing runtime state.
  Reset clears both the transcript and stack, variables, definitions, and
  imports. These are separate actions; resetting preserves Enter lock.
- State inspector corresponds to the source editor's cursor position.
- State inspection shows static types and stack effects without execution.
- Show “Updating…” only if analysis takes longer than 500 ms.
- Multiple overload/state possibilities use separate pages and a navigation bar,
  such as “View overload 1”, “View overload 2”.
- A compile-blocked Alt+X keeps focus in the editor and reveals Errors.
- When the caret is on an element, show its resolved overload and documentation,
  not the stack. Cursor positions before/after it expose stack-state inspection.
- Cursor hit rules: an element's characters select element inspection;
  surrounding whitespace selects stack inspection. Enclosing function context
  appears only in stack view, not element view.
- Inspection alternatives have a clickable page bar and Previous/Next controls;
  overload signatures identify overload pages.
- Provide named themes, independent styling controls, and Restore defaults.
- Automatic indentation and completion are undoable together with the
  typing/action that triggered them.
- Stack view lists the top at the top and scrolls for deep stacks.
- Last Element shows the operation name and resolved stack-effect signature;
  include literal pushes and show the type of a referenced variable.
- Overload-world selection stays synchronized across cursor positions within
  a function. Offer only worlds that survive analysis through the function's end.
- Show “State unavailable” at positions without valid analysis; retain inspection
  in independently valid regions.
- Editing is allowed during execution. Runs keep their original source snapshot;
  edits require an explicit Alt+X to reload. Stop active execution before another
  Alt+X; do not queue repeated loads.
- Inspector includes function context, stack state, last operation information,
  selected element documentation/resolved overload/other overloads, and errors.
- Selecting an error shows existing diagnostic detail and highlights its source.
  Expanded details dim unrelated code; restore normal brightness immediately
  when the cursor moves to non-error code, or when details minimize/dismiss.
- Keep a bounded REPL transcript so heavy output remains responsive, and provide
  an option to save the transcript.
- Show a marker when older transcript output has been dropped.
- Styling is customizable, including independent controls for individual UI
  parts. Use the screenshots' dark palette by default, with a reduced-color
  terminal fallback.
- When errors appear, the inspector keeps following the cursor rather than
  automatically switching to the error list. Users can show or hide Errors.
- Errors covers the active file and its imported dependencies, grouped by file.
  Selecting an error opens or activates its source file at the diagnostic
  location and displays its details.
- Warnings and lints must be very unobtrusive: advisory, visually secondary,
  without automatic view opening or focus changes, and never blocking Alt+X.
- A selected error shows expanded details. Moving the editor cursor elsewhere
  minimizes those details to a pinned summary and resumes cursor inspection.
  Any source edit dismisses the selected error/pin so outdated detail does not persist
  while the user fixes the program.
- Users can hide REPL or inspector, use just the editor, or use REPL alone.
- Pane dividers support mouse dragging and keyboard resize commands. Preserve
  chosen pane sizes when panes are hidden and reopened within the session.
- Provide menu toggles for REPL and State and a REPL-only mode, restoring prior
  pane sizes when returning.
- Keep the UI responsive during execution and provide Stop. Ctrl+C stops code
  while it is running; otherwise it copies selected text.
- When the terminal is too narrow for usable side-by-side panes, automatically
  collapse them into switchable views with editor priority. Preserve hidden
  pane contents and keep REPL and inspector accessible.
- This is a TUI that works in a terminal. HTML/CSS would be a design reference.

## Visual reference map

References supplied by the user are retained in the repository under
`docs/maintenance/assets/terminal-editor/`. The relative links below work across
checkouts; the filenames supersede the initial clipboard filenames.

| Reference | Intended state |
| --- | --- |
| [Hero Example.PNG](assets/terminal-editor/Hero%20Example.PNG) | Default tabs, editor, inspector, and REPL |
| [Inside a function.PNG](assets/terminal-editor/Inside%20a%20function.PNG) | Cursor inside `{Circle as Shape}.area` |
| [Inside another function.PNG](assets/terminal-editor/Inside%20another%20function.PNG) | Cursor inside `{Circle as Shape}.perimeter` |
| [Cursor on a specific element.PNG](assets/terminal-editor/Cursor%20on%20a%20specific%20element.PNG) | Cursor on `**`; overload inspection |
| [REPL only.PNG](assets/terminal-editor/REPL%20only.PNG) | REPL occupies the terminal; editor UI hidden |
| [No REPL.PNG](assets/terminal-editor/No%20REPL.PNG) | Tabs, editor, and inspector |
| [Errors present.PNG](assets/terminal-editor/Errors%20present.PNG) | Inspector lists program errors |
| [Clicked on an error.PNG](assets/terminal-editor/Clicked%20on%20an%20error.PNG) | Selected diagnostic, source emphasis, help |
| [Just the editor.PNG](assets/terminal-editor/Just%20the%20editor.PNG) | Tabs and editor only |

The examples establish visual intent, not language semantics. Example types,
overload signatures, and placeholder help prose must not become hardcoded data.
The red I shape illustrates cursor position, not a required caret color.
These repository copies are the visual references for subsequent planning and
prototypes; no personal filesystem paths are needed to access them.

## Layout and terminal styling proposals

The hero reference has a full-width tab strip, editor on the upper left,
inspector on the upper right, and full-width REPL below. The inspector is about
one quarter of the image width; the REPL is about one third of its height.
These are starting proportions, not fixed pixel dimensions.

Proposed terminal translation:

- Measure layout in character cells. Use compact toolbar labels aligned with
  file tabs, an inset from the top edge, a separate active-tab underline and thin
  dividers. Menus overlay the editor at their anchors without a dimmed backdrop
  or a transient frame at a different position.
- Keep editor and REPL backgrounds near black; gutter slightly lighter; tab
  strip dark gray. Use magenta keywords, cyan types, orange numbers, muted
  comments, light source text, and green REPL results as in the references.
- Use terminal monospace throughout. Section labels may use bold/underline;
  signatures and function fields must remain readable without larger fonts.
- Use compact labeled fields for function context when a table will not fit.
- Wrap inspector prose and signatures; scroll long lists. Source lines wrap
  visually by default, with a toggle to use horizontal scrolling instead.
  Preserve source text and diagnostic coordinates across display modes.
- Support keyboard access to every action, with mouse selection as an addition.
- Distinguish focus, active tab, dirty document, and diagnostics through markers
  as well as color. Specify reduced-color and ASCII border fallbacks.
- Hiding a pane preserves its content. REPL-only mode preserves open documents
  and exposes a keyboard action to restore editor UI.
- Dividers are resizable by mouse and keyboard. Restore chosen sizes when a
  pane is reopened, constrained by available terminal dimensions and minimum
  usable pane sizes.
- Narrow terminals automatically use switchable views rather than squeezing
  panes below usable widths. Prioritize the editor on entering this layout;
  users can switch to REPL or inspector. Successful Alt+X still reveals and
  focuses the REPL. Temporary responsive collapse preserves document/session
  state and is distinct from explicitly hiding a pane.

Open: resize shortcuts, minimum pane sizes, responsive breakpoints and behavior
when space becomes available again, persisting
user dimensions across launches, tab overflow, focus indicator, theme configuration,
and exact palette/contrast. Prototype at 80x24, 120x40, and 160x50 cells, rather
than relying on the 1280x720 reference images.

Confirmed styling direction: use the screenshots' dark palette by default,
support custom themes and independent styling of individual UI parts, and
provide a reduced-color terminal fallback. Proposed configurable roles include
pane backgrounds, text, gutter, active/inactive tabs, dividers, syntax categories,
diagnostics, error dimming, REPL output, focus, status row, and completion.
Exact configuration format, scope, and theme editing controls remain open.

Confirmed customization: independently style tabs, gutter, syntax categories,
panes, diagnostics, REPL output, and status row. Support named themes and Restore
defaults. Additional configurable roles listed above remain proposals.

## Documents and file actions

Confirmed: Save and Load are separate actions. Closing an unsaved document
offers Save, Discard, or Cancel; Quit protects all unsaved documents. Save on an
untitled document opens Save As. Cancel or a failed save prevents closing.
An untouched blank untitled document does not require a save prompt.

Confirmed file shortcuts:

| Shortcut | Action |
| --- | --- |
| Ctrl+S | Save the active document; Save As if untitled |
| Ctrl+Shift+S | Save As |
| Ctrl+O | Open a file |
| Ctrl+N | Create an untitled tab |
| Ctrl+W | Close the active tab, prompting for unsaved changes |
| Ctrl+Q | Quit, protecting all unsaved documents |

All actions are also available through the file menu. Verify key delivery in
supported terminals; some do not distinguish Ctrl+Shift+S from Ctrl+S. Preserve
menu access and specify a distinguishable alternative binding where necessary.

Confirmed: closing the last tab leaves a new blank untitled document. Closing
and quitting use the Save / Discard / Cancel behavior described above; cancelling
keeps the document/application open.

Confirmed: Open reuses and activates an existing tab for the same file, preserving
its unsaved text and editing state. Different files sharing a basename use path
information in their tab labels to distinguish them. Determine file identity
through normalized paths appropriate to the platform; exact label shortening
and behavior for aliases/symlinks remain open.

Confirmed: external changes always offer Reload / Keep editor text. Never
automatically reload an open document, including one without unsaved edits.
Reload is explicit; any unsaved-change protection still applies.

Confirmed: Ctrl+F searches the active document and Ctrl+H opens replace.
Search/replace scope options, matching controls, and replacement confirmation
remain to specify.

Confirmed: Ctrl+PageUp / Ctrl+PageDown switch file tabs. A status row shows
cursor line/column, unsaved state, error count, and which file initialized the
REPL. Loaded-source revision/staleness presentation remains to specify.

Proposed: each document owns text, file identity, dirty state, undo history,
cursor/selection, scroll offset, and current analysis revision. Switching tabs
does not execute source. Saving does not execute source. Inspector follows the
active document; the REPL shows which document/revision initialized its session.

Open decisions:

- New document naming.
- Multi-document quit prompt presentation and ordering; quitting must remain
  cancellable without losing documents whose changes have not been saved or
  explicitly discarded.
- Open/Save As path picker, relative-path base, extension and encoding policy.
- File aliases/symlinks, path-label shortening, external-change prompt presentation
  and handling of conflicts when saving,
  failed writes, missing files, and read-only files.
- Recent-file ordering/storage/limit; optional explicit workspace/session
  restoration (startup itself always opens a blank untitled document).
- Editing: indentation, tabs/spaces, newline preservation, selection, clipboard,
  undo/redo, search, and bracket support.
- Wrapped-line navigation, continuation indentation/gutter markers, wrap-toggle
  shortcut, and whether the wrap preference is per document or application-wide.

Confirmed undo behavior: automatic indentation and completion participate in
undo with their triggering typing/action, rather than requiring users to undo
automatic edits separately. Exact typing coalescing and selection restoration
remain to specify; undo must preserve this relationship for immediate dedent.

## Completion

Confirmed: automatically show completion when there are 1–8 suggestions based
on the type-stack context at the cursor. No candidates means no popup; more than
eight candidates means no automatic popup. Count candidates after contextual
filtering, not by truncating a larger result set to eight.

Confirmed: the same policy applies to REPL input. Its semantic context starts
from the current session's stack, variables, definitions, and imports, then
statically analyses the draft command to determine context at the cursor.
Preview analysis must not execute the draft or mutate the persistent session.
Invalidate REPL completion context when either the draft or session changes.

Proposed: combine the typed prefix with semantic context, including stack types,
scope, visibility, and the syntactic position. Use the same current analysis
snapshot and overload applicability rules as cursor inspection. Do not infer
stack state from a textual prefix or use the live REPL stack for source-editor
completion. Automatic suggestions must never insert text without acceptance.

Confirmed: group multiple overloads of the same element into one suggestion.
Count that entry once toward the automatic-popup threshold, regardless of its
overload count. Show overload details for the entry rather than repeating the
element name in separate suggestions.

Confirmed: Ctrl+Space explicitly opens a scrollable completion list, including
candidate sets larger than eight. Filter the list as the user types. The eight-
item threshold governs automatic opening only; it does not truncate explicitly
requested results. This applies to source-editor and REPL input.

Confirmed: while completion is open, Up/Down navigate its suggestions. This
takes precedence over editor cursor movement and REPL history navigation,
including when Enter lock is enabled. Escape closes completion and restores
the normal Up/Down rules without changing the input or lock setting.

Open: overload-detail
presentation, ordering, and behavior when context is unavailable or stale.

Confirmed completion keys: Tab accepts the selected suggestion; Escape dismisses
the popup. Enter never accepts a suggestion and keeps its existing newline or
submission meaning. Without a completion popup, Tab inserts indentation in the
source editor and in REPL input with Enter lock enabled. F6 cycles focus between
visible panes rather than using Tab for pane navigation.

Confirmed: use spaces for indentation, two per level by default, with a
configurable width. Ctrl+[ dedents and Ctrl+] indents; do not substitute
Shift+Tab as the specified dedent binding.

Proposed: indent/dedent acts on the current line or all selected lines. Preserve
the logical selection and apply one indentation level per invocation.

Confirmed: automatic indentation follows syntactic context rather than blindly
copying the previous line. Enter carries relevant indentation into the next
line; closing constructs such as `end` dedent to the matching block. Apply this
in source-editor input and when Enter inserts a newline in locked REPL input.

Implementation requirement: derive block structure from shared language syntax
tooling, using tolerant context for unfinished input. Do not treat text inside
strings/comments as block keywords or maintain a separate TUI grammar.

Open: exact block-opening/continuation indentation rules, the moment automatic
dedent occurs for other closing constructs, incomplete-syntax fallback, and treatment of manually adjusted
indentation. Preserve the configurable indentation width throughout.

Confirmed timing for `end`: dedent its line as soon as it is recognized as the
completed closing keyword, rather than waiting for Enter. Recognition must use
syntax context and token boundaries; typing a longer identifier must not leave
it incorrectly dedented because it temporarily had the prefix `end`.

Open: Tab behavior in unlocked REPL input, selection indent/dedent details,
focus-cycle order, and reverse focus cycling. File menus and
dialogs need their own keyboard-navigation rules.

## Loading into the REPL

Confirmed compile gate and successful-load contract:

1. Alt+X targets the entire active document, not a selection or current line.
2. Compile errors prevent the action. Validate the current source and dependency
   revisions before execution; pending or stale analysis is not proof that the
   current program is valid. A blocked attempt exposes diagnostics and leaves
   the existing REPL session untouched.
3. Once compilation succeeds, execute it from fresh runtime state.
4. Its resulting stack, globals, definitions, and imports become the session
   used by subsequent REPL submissions.
5. Loading again starts fresh, discarding prior REPL state on a successful load.
6. Program output is routed to the REPL. Successful loading reveals it if hidden
   and moves keyboard focus to its command input.
7. Retain earlier transcript entries and insert a session separator before the
   new program's output. Earlier output remains historical; runtime state starts
   fresh. Compile-blocked attempts do not insert a new-session separator.

Confirmed: run current unsaved editor text, with no implicit save. Save never
loads or executes the document.

Confirmed: imported files that are open in the editor use their current text,
including unsaved changes. Other imports use saved disk source. Analysis and
execution must use the same source revisions across the import graph; edits to
an open dependency invalidate dependent diagnostics and compilation snapshots.
Compile errors in those dependencies also prevent Alt+X.

Confirmed: each file's relative imports use that file's directory, including
imports inside dependencies. Documents without a file path use the captured
launch directory. Opening a file or saving an untitled document establishes its
file-based import directory; Save As changes that directory. Reanalyse affected
imports when file identity changes. The launch directory is a fallback for
documents without a path, not a second search directory after a file-relative
import fails.

Proposals to review:

- Runtime faults are execution faults, separate from the compile gate. Reset
  runtime state and preserve/mark output. Output and external effects cannot be
  undone by resetting runtime state.
- Do not silently reload after edits or when changing visible panes.
- Show loaded document/revision and whether current editor text differs from it.
- Confirmed: a compile-blocked Alt+X keeps keyboard focus in the editor and
  reveals Errors without starting a new runtime session.

Confirmed: bound the retained REPL transcript to keep heavy output responsive
and provide a Save Transcript action. Show a marker when older output is dropped.
Save Transcript exports currently retained output, including truncation markers.
It does not maintain or export a separate complete log. Exact size limits, marker
wording, and rendering/backpressure policy remain open.

Open: separator appearance/label, output ordering and
separation of printed output from stack results, imports, repeated load while
busy, cancellation timing, stdin requests, transcript limits,
and loading with existing REPL branches open.

Confirmed execution interaction: keep the UI responsive while code runs and
provide a Stop action. Ctrl+C stops execution while running; otherwise it copies
selected text. Stop takes precedence over Copy while execution is active.
Cancellation must be an execution-service operation, not a frontend grammar or
VM workaround. Stop or a runtime fault resets runtime state while preserving
output and marking the interruption. History and Enter lock are preserved.
Cancellation timing remains open.

Confirmed execution snapshots: allow editing during execution without changing
the source/dependencies used by the current run. Edits never automatically
reload; Alt+X is required. While execution is active, block a new Alt+X and
require stopping first; do not queue another load.

Confirmed pane controls: menu toggles for REPL and State, plus REPL-only mode.
Restore previous pane sizes when returning, constrained by terminal dimensions.

## REPL interaction

Confirmed: one application-wide REPL session. Switching editor tabs does not
switch, reset, or reload it. The inspector follows the active editor document;
the REPL continues using its current loaded program until another explicit
Alt+X or Reset. Existing branch/type/help commands need a migration decision.

Confirmed: Clear removes the visible REPL transcript without changing stack,
variables, definitions, or imports. Reset clears both the transcript and those
runtime components. It does not retain old output or insert a reset separator
into an otherwise preserved transcript. Clear and Reset are distinct actions;
Reset preserves Enter lock. Neither action erases persisted command history.

Confirmed submission model: Enter lock, replacing the double-Enter proposal.

- Start unlocked, so Enter submits by default.
- Preserve the current lock setting after submission; do not automatically
  change it when a command succeeds or fails.
- Preserve it across Alt+X and REPL reset as well. Only an explicit toggle
  changes it within the application session; new launches start unlocked.
- Lock on: Enter inserts a newline, including intentional blank lines, without
  submitting.
- Lock off: Enter submits the input.
- Toggling the lock preserves the input and never submits by itself. Users can
  compose multiline input with the lock on, turn it off, then press Enter to run.
- This toggle belongs to REPL input; Enter in the source editor adds newlines.
- Ctrl+Enter and Shift+Enter toggle the lock. Display its state beside REPL input; toggling
  does not submit. Keep a menu alternative for terminals that cannot distinguish
  modified Enter from Enter.

Confirmed Up/Down behavior:

- Unlocked: navigate input lines normally; Up on the first line and Down on the
  last line navigate command history.
- Locked: navigate only within the current command. At its first/last line,
  remain in the command instead of accessing history.

Confirmed: preserve the current draft when entering history and restore it when
returning past the newest entry.

Confirmed: command history persists across launches. New launches still open
a blank untitled document with fresh runtime state and Enter lock disabled.
Persisted history is available for recall, not automatic replay.

Proposed: recalling history edits input only; it never executes automatically.
Decide separate explicit history access for locked mode and whether draft
restoration includes cursor and selection as well as text.

Confirmed: show a persistent indicator beside REPL input and use Ctrl+Enter to
toggle Enter lock. Proposed indicator text: `Enter: newline [locked]` or
`Enter: submit`. Provide a menu action as well.

Proposed: pasted newlines must never trigger submission. Empty submissions should
not execute or create history entries. Submitting incomplete syntax should
report diagnostics while preserving the draft for correction.

Open: indicator styling,
history storage location/limit/clearing, draft cursor/selection restoration and explicit history access,
editing old input,
selection/copy, completion, scrollback, empty-stack rendering, truncation of
large values, command namespace, and focus cycling. Alt+X requires an accessible
menu/key alternative for terminals that cannot deliver that binding reliably.

## Cursor inspection

Confirmed: inspection uses static inferred types and stack effects, without
execution. It can describe a function that has never run.

Confirmed: when the caret is on an element, hide stack state and prioritize its
resolved overload, overload-specific documentation, and other overloads.
Positions before and after an element show static stack state at that boundary.
Exact insertion-point rules remain open: a terminal caret occupies a cell and
token-edge positions must be distinguishable from being on an element.

Confirmed hit rules: when the caret occupies an element's characters, show its
element view. In surrounding whitespace, show stack state at the corresponding
boundary. At an adjacent token with no whitespace, use the token under the caret;
exact boundary/end-of-line conventions remain to specify.

Confirmed function context: show the enclosing function's name, parameters,
and return types only in stack view. Element view describes the specific selected
element and its overloads, without the enclosing function context.

“Last Element” must refer to the semantic operation preceding the inspected
boundary, not automatically the source token to the left. Literal pushes are
operations too. Show the name and resolved stack-effect signature; for variable
references, show the variable's type as well. At a chain boundary, use the
semantic checkpoint specified below, not the nearest source token to the left.

Confirmed stack rendering: top at the top, as in the screenshots, with scrolling
for stacks that exceed the available pane height. Show “State unavailable” where
analysis cannot provide a valid state, while retaining inspection in valid
regions. Unavailable is distinct from an empty stack.

Required specification work:

- Remaining cursor hit rules at token edges, in comments, on declarations,
  and at beginning/end of a block.
- Mapping lowered execution order back to source ranges. Analysing a text
  prefix is insufficient for chains whose source/execution order differs.
- Function scope: parameters, inferred inputs, local variables, return effects,
  generics, nested functions, and branch-specific possibilities.
- Distinguish empty stack, unavailable analysis, unreachable code, and multiple
  valid branch states. Do not invent one state when several are possible. Use
  separate inspection pages with a navigation bar for multiple possibilities.
- Selected overload documentation must come from the chosen overload. Other
  declarations are not necessarily applicable alternatives at this call site.
- Define inspector view priority: cursor context, explicit element inspection,
  explicitly opened error list, and selected error. Errors appearing must not
  automatically replace cursor inspection. Selected error details minimize to
  a pin when the cursor moves elsewhere; any source edit dismisses the selection/pin.
  The compact pin sits above cursor inspection and offers expand/dismiss actions.
- Specify behavior during incomplete edits, stale analysis, and failed analysis.

Confirmed analysis progress: show “Updating…” only when analysis of the current
revision takes longer than 500 ms. Cancel the pending indicator if analysis
finishes sooner, and reset the timer for a new revision. Never present older
revision facts as if they describe current text; the interim presentation before
the indicator appears remains to specify.

Confirmed multiple-result presentation: use pages with a navigation bar, such
as “View overload 1”, “View overload 2”, to inspect each overload/state
possibility separately. Each page keeps its function/overload context and stack
facts together as appropriate to the view: enclosing function context appears
only in stack view. Use a compact numbered page bar and Previous/Next controls within the inspector.
Identify the selected overload through its resolved signature and stack/function
facts; page controls may expose signatures in their accessible descriptions. Some alternatives arise from control-flow paths rather than
overloads; their exact labels remain to specify without misidentifying them as
overloads. Keyboard bindings remain open.

Confirmed world selection: synchronize the selected overload world across
cursor positions in the same function. Selecting world 3 at one position keeps
world 3 selected elsewhere; page selection must identify the semantic world,
not merely the current list index. Show only overload worlds whose analysis
survives through the end of the function, including its return contract. Do not
offer a locally viable world that fails later. Preserve trace facts along each
surviving world so earlier positions can be inspected consistently.

If an edit removes the selected world, silently select the first surviving
world; no fallback notice is needed. No surviving worlds is already a compiler
error regardless of frontend: no overload can be generated. The TUI displays
that diagnostic and blocks Alt+X; it does not introduce a new error rule.
This world-trace product is an analyser capability to design, not presumed to be
provided by existing hover or final typed AST APIs.

## REPL result and load-error presentation

Confirmed: green is reserved for REPL results. Commands, printed program output,
session separators, progress text, and diagnostics use their own neutral or
severity styles. Transcript entries separate submitted source from results so
an entire command/result block is not colored green.

Confirmed: compile errors preventing Alt+X have a visible indication in the REPL.
The prototype shows a persistent load-blocked indicator for the active document
and records attempted blocked loads with their diagnostics in the transcript.
Keep editor focus, preserve the existing runtime session, and do not create a
fresh-session separator for a blocked attempt. Clicking the indicator opens
Errors. Fixing the source clears the live indicator; historical transcript
entries remain historical.

## Diagnostics

Confirmed: list all program errors and allow selection of individual errors.
“All” means all diagnostics the compiler can recover and report; some malformed
regions may prevent analysis of dependent code.

Confirmed scope: the active file and its imported dependencies, grouped by file.
Selecting an error opens its file if needed, or activates its existing tab,
and navigates to the diagnostic location. Reuse an already open document and
its current text rather than opening a duplicate disk-backed tab.

Confirmed: new errors do not automatically take over the inspector; it keeps
following the cursor. The Errors view is user-controlled and can be hidden.
Confirmed: show the Errors tab and error-count entry points only while the
active file or its import closure has errors. With no errors, hide them and
return an open Errors view to cursor inspection. New errors make the entry point
available without forcing the list open. Historical REPL diagnostic entries
remain in the transcript after errors are fixed.

Confirmed constraint: warnings and lints must be very unobtrusive. They remain
advisory and do not block Alt+X, take focus, or automatically open a view. Their
presentation must be visually secondary to errors.

Proposed: show errors by default in the diagnostic list, with optional filters
to reveal warnings/lints. Avoid warning popups or prominent inline prose; any
summary indicator should be compact and subdued. Exact markers, count placement,
and whether advisory source highlighting is enabled by default remain open.

Proposed: reuse structured diagnostics, source ranges, related locations,
stack evidence, and existing help. Supply keyboard navigation equivalent to
clicking. Keep errors distinguishable without color; never invent help text.

Confirmed selected-error lifecycle:

- Clicking an underlined source error opens that diagnostic in the Errors tab.

- Selecting an error opens its full diagnostic details and emphasizes its source.
- Moving the editor cursor elsewhere minimizes the error to a pinned summary;
  normal cursor inspection resumes so surrounding stack state can be inspected.
- The compact pin sits above cursor inspection and shows filename, line, and
  a short message. Selecting it expands the details; a dismiss control removes it.
- Any source edit dismisses the selected error and its pin, including typing,
  deletion, paste, undo, and redo. Fresh analysis determines
  whether the error still exists; dismissal does not itself remove the error
  from diagnostics or declare the program valid.

Confirmed styling: expanded selected-error details dim unrelated code. Restore
normal brightness the moment the editor cursor moves to non-error code; do not
wait for typing or explicit dismissal. Minimized/dismissed details also remove
dimming. Keep the diagnostic's own source marker visible while it still exists.

Proposed: also dismiss on changes in imported files that invalidate the
diagnostic. Exact dimming strength, relevant-range identification, pin styling,
and keyboard expand/dismiss shortcuts remain open.

Open: advisory filters/highlighting, ordering within file groups,
error-count placement, source scrolling/selection on activation, multiple errors
on one line, dimming strength/relevant ranges, and the minimized pin's styling/shortcuts.
Runtime errors belong to the executed revision, which may differ
from the editor's current revision.

## Existing implementation and architectural constraints

Reviewed maintenance architecture, relevant type/runtime explanations, compiler
guides, and the current REPL orchestration:

- `src/valiance/main.py`: no-argument dispatch and `_ReplSession`; persistent
  commands and execution. Current scratch submissions reset the session.
- `src/valiance/repl.py`: prompt-toolkit frontends, highlighting, completion,
  scratch editing, and live diagnostics. `pyproject.toml` already declares
  prompt-toolkit. The user selected Textual with a fresh frontend rather than
  extending this code. The staged plan validates Textual editing and worker
  integration before building the new application.
- `src/valiance/lsp.py`: overload-aware hover, documentation, variable types,
  completion, and source navigation. Extract reusable semantic services where
  appropriate instead of copying presentation-specific behavior.
- `CompilationDatabase`: shared source overlays and coherent analysis/executable
  snapshots; see [its guide](unified-compilation-database.md).
- Parser recovery supports multiple lexical/grammatical diagnostics. Analysis
  uses branch sets; typed nodes carry selected overload decisions.

Keep document state, semantic snapshots, runtime session, and presentation
separate. Bind diagnostics/inspection to document revisions; reject late results
for older text. Cursor movement should query a current snapshot rather than
rerunning the VM. No second parser, overload resolver, or evaluator in the TUI.
Full cursor-state coverage is a capability to design, not assumed to exist today.

Framework decision: use Textual and build the terminal frontend from scratch.
Keep the existing compiler and runtime behind shared services; do not wrap the
prompt-toolkit UI. Validate editing quality, pane layout, Windows/SSH support,
mouse behavior, resize, clipboard, key delivery, and execution responsiveness
in the first stage.

## Planning sequence and completion criteria

1. Agree load/session semantics and cursor-state meaning.
2. Specify document lifecycle, REPL interaction, and diagnostic navigation.
3. Specify keyboard/focus behavior, layout adaptation, and terminal styles.
4. Create an HTML/CSS design reference with all nine named scenarios and terminal
   cell-size previews. Clearly label simulated semantic data; it is a prototype.
5. Record semantic API boundaries, execution/cancellation model, implementation
   stages, and acceptance scenarios before implementation is authorized.

Acceptance scenarios must cover every reference view plus successful load,
compile-error prevention, runtime faults during execution,
reload, dirty quit, unsaved imports, invalid/incomplete source, branches,
ambiguous overloads, stale results, hidden panes, narrow terminals, cancellation,
output flood, and keyboard-only operation. During eventual implementation, keep
`tests/test_programs.py` unchanged and passing; add coverage to appropriate
existing REPL, CLI, LSP, compilation-database, and analyser suites.

Planning is complete when consequential decisions above have agreed behaviors,
the visual reference is reviewable, and every behavior has acceptance criteria.

## Feedback from interactive prototype review

These are confirmed requirements for the terminal editor as well as the HTML
reference. The prototype uses fixture analysis; the TUI must obtain accurate
semantic facts from the compiler.

- Clicking an underlined source error selects that diagnostic in the Errors tab,
  navigates to its location, and opens its details. Moving to non-error code
  immediately restores normal source styling and minimizes the diagnostic pin;
  editing dismisses it.
- Green identifies REPL results only. Submitted commands, printed program output,
  separators, and errors have separate styles.
- Compile errors have a visible blocked-load indication in the REPL. Attempting
  Alt+X exposes the errors without replacing the existing runtime session.
- Stack inspection must vary with the cursor's semantic location. In the Shapes
  example, the assignment and `println` leave an empty stack; the standalone
  `$circle` pushes `Circle`; the final pipeline leaves `Real` above `Circle`.
  Blank lines after that pipeline retain that final stack. It must not show
  the final stack at every location in the document.
- Chain boundaries need source-to-evaluation mapping. In
  `println perimeter $circle`, the gap between `perimeter` and `$circle` shows
  `Circle`: the right-hand operand has been evaluated, and `perimeter` has not.
  The gap between `println` and `perimeter` shows `Real`, before printing.
  Last Element at these boundaries reflects the evaluated operand, including
  `$circle`'s type, rather than whichever token lies to the left. Element hit
  rules still show resolved overload information when the caret is on a token.
- Errors and their entry points appear only when errors exist; they do not
  displace cursor inspection automatically.
- Provide several named color themes in addition to independently configurable
  style roles. The reference offers Reference dark, High contrast, Ocean, Plum,
  Amber, and Paper to explore appearance; precise shipped palettes remain a
  styling choice. Default appearance follows the original screenshots.
- Each file tab offers Rename and Close, with keyboard-accessible file-menu
  equivalents. Close uses the existing Save / Discard / Cancel flow for dirty
  documents. Renaming preserves buffer text, dirty state, caret, and undo history.
  For a saved file, the TUI renames its filesystem entry in the same directory;
  failures or conflicting destinations leave its identity intact. Rename does
  not implicitly save edited contents or rewrite import statements. Refresh
  source identity and relative-import analysis, and require Alt+X to reload the
  runtime. An untitled document can change its suggested name without saving.
  The browser reference changes document identity only; it cannot rename disk
  files and says so in the rename dialog.

Confirmed follow-up from prototype review:

- Do not put an explicit pencil icon on file tabs. Rename is available by
  double-clicking the tab name and through the file menu; Close retains its
  compact tab control. Both actions have keyboard-accessible equivalents.
- The REPL error banner describes the file and compile errors, without the
  redundant “Alt+X blocked” wording. Errors still prevent loading and the banner
  still opens diagnostics.
- Shift+Enter also toggles Enter lock in REPL input, just like Ctrl+Enter. Either
  shortcut preserves draft text and never submits or inserts a newline itself.
  Unmodified Enter retains the current lock behavior. Source-editor Enter
  behavior is unchanged. Provide a menu toggle when terminal key reporting
  cannot distinguish these chords.
- Overload branching is integrated into ordinary cursor inspection. Show a
  small, subdued `Overload ‹ 1 2 3 ›` page control only when multiple surviving
  worlds exist. The selected number is emphasized; the rest of the inspector
  shows that world's stack, resolved effect, and applicable function context.
  Keep selection synchronized as the cursor moves. Avoid large overload cards,
  a separate walkthrough banner, or checkpoint buttons. The Overloads.vlnc
  reference tab illustrates this through normal cursor movement, with the
  first inspected position already inside the function.

## Feedback from terminal implementation review

- REPL output and editable input have separate titled borders and contrasting
  backgrounds. Keep submission/newline guidance visible in the input title.
- Make pane resizing discoverable with directional handles, hover feedback,
  keyboard access and a “Drag to resize” label.
- Keep toolbar controls compact. Align their text and tabs on the same inset row.
- Keep File, Edit and View actions separate; dropdowns appear under their own
  controls, are constrained before drawing and leave background panes unchanged.
  Mouse dismissal must not require executing a menu action.
- Run remains available through F5, Alt+X and a toolbar button; Stop uses F8 and
  its button. Execution belongs in the persistent worker, never on the UI loop.
