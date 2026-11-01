# Paper workbench

The React WebUI is the research editor; GPUI remains the native project and service entry. Paper, source, PDF and Diff own the screen. The research assistant is closed until asked for and never replaces the editor.

## Layout and modules

Top bar: project, save state, compile state, version and state revision, engine, compile, assistant. Left rail: files, outline, literature, versions. Center: source, review, workflow, evidence. Right: PDF. Panes are rounded sheets on a sunken desk, resizable and remembered.

Width decides how they share the screen, and the same components stay mounted throughout, so a resize never loses the paper's state:

- **Above 1180 px:** rail, source and PDF side by side.
- **Up to 1180 px:** source and PDF keep the width. The rail becomes a frosted drawer, opened from the top bar or with Ctrl+B, and steps aside once a place in the paper is opened.

Nothing floating takes space from the panes:

- **Drawers:** the research assistant slides in from the right over the PDF, and the compile log rises from its bar over the source. Both close with Esc or their shortcut. A failed build opens the log; the next success puts it away unless the user opened it.
- **Widgets:** project switcher, model picker, new file, find and replace, shortcuts, tips and `<select>` menus.
- **Frosted glass:** all of the above use `.glass` (`backdrop-filter`). A widget inside a drawer uses a near-opaque ground, because blur cannot reach through a frosted parent. Without `backdrop-filter`, or with `prefers-reduced-transparency`, surfaces are opaque. The original brief excluded glass; this was changed at the user's request and applies only to floating surfaces.

| Module | Responsibility |
| --- | --- |
| `frontend/workspace.ts` | Controller. Owns project, run, build and SyncTeX tokens, snapshot guards and drafts |
| `frontend/ui/source-editor.tsx` | CodeMirror 6: LaTeX highlighting, line numbers, search and replace, history, IME-safe external updates |
| `frontend/ui/pdf-viewer.tsx` | PDF.js in its worker: paging, zoom, text layer, forward target, double-click inverse search |
| `frontend/ui/diff-review.tsx` | Files, location, original, new, inline diff, rationale, source run, base version, status, conflict |
| `frontend/ui/evidence.tsx`, `frontend/models/evidence-model.ts` | One row per claim; each says what it does not mean |
| `frontend/ui/assistant.tsx` | Five modes with different inputs and tools; provider and model picker |
| `frontend/ui/icons.tsx` | One Lucide icon per function, file type, status and workflow stage |
| `frontend/i18n.ts`, `frontend/locales/en.ts` | Interface language |
| `frontend/models/*` | Pure logic, tested without a DOM |

`web/workbench.css` holds the design tokens for both themes; `native/src/main.rs` carries the same values in `Palette`. Fonts are Keel Sans, Keel Mono and Keel Display (Keel type 0.9, proprietary, `assets/fonts/keel/`), self-hosted on the web and embedded in the native binary, with the system CJK font as fallback because Keel has no Chinese glyphs. `assets/brand/` holds the application icon drawings; `scripts/build_icons.mjs` derives the favicon, the Windows `.ico` and the GPUI assets from them.

## Model picker

`GET /api/runtime/models` returns only what each backend reports: Codex from `codex debug models` (entries marked `list`, by priority), Claude Code from the aliases named in `claude --help`, because it has no catalog command, and the API fallback from `LEMFOLIO_API_MODEL`. A backend that cannot say lists nothing. The CLI default, which passes no `--model`, and a typed model ID are always available. Changing backend clears the model. `codex debug models` is a debugging command rather than a stable interface; if it changes, the list empties instead of breaking runs.

## Runs in the assistant drawer

- **Claude runs** show the run's workflow as a graph, read from `GET /api/projects/{id}/workflow?run_id=`: eight nodes and their edges, with the status the backend reports. Drag a node to move it; click it to read its record. Positions are held as offsets from the default layout, so a narrower drawer reflows the graph, and they last only as long as the window. They are not evidence of anything. The graph refreshes when the run's status changes and after a proposal, decision, verification or build, not on every poll, because the projection re-reads evidence and artifacts.
- **Codex runs** show the reasoning summary the CLI reports and the suggested changes as a diff. An edit shows only the lines that moved. A whole-file replacement has no old side until a proposal exists, and says so. Each file shows 40 lines at most and counts the rest; Review remains the place to decide.
- **Effort** sits in the model picker, and the selection shows on the picker's control and on each run. Levels come from the provider: `codex debug models` per model, `claude --help` for all aliases. With no model chosen, only levels every listed model accepts are offered. Claude Code receives `--effort`, Codex `-c model_reasoning_effort="…"`. A level the next model does not offer falls back to the CLI default. The selection is not remembered across windows, and the web workbench has no effort control.

## Language

Chinese source text is the message key, wrapped in `t()`. `frontend/locales/en.ts` holds English, including the reasons the Python workflow projection reports, which are looked up at display time. The language follows the stored choice, then the browser: `zh*` gets Chinese and anything else English. Switching reloads the page, keeps the project through the `#project=` address, and is disabled while edits are unsaved. `tests/web_i18n.mjs` fails when a message lacks English, placeholders differ, or Chinese bypasses `t()`. To add a language, add a dictionary and extend `Locale`.

GPUI follows the system language and accepts `--lang zh|en`; a unit test checks its table against its source.

## Guarantees kept from the hardened baseline

- Saves match the previous hash. A proposal binds its digest and base state; an out-of-date proposal shows the conflict and cannot be accepted, though it can still be rejected. `GET /api/projects/{id}/proposals/{id}/conflict` reports both versions and what really moved in the proposal's own files. It recovers the base text by undoing the proposal's stored diff and claims nothing if that does not reproduce the diff exactly.
- Applying a file and verifying it are separate results, shown separately.
- A new, failed or cancelled build and an entry change clear the preview. Historical builds and runs are labelled and never count as current evidence.
- Late responses cannot replace a newer selection. This includes run lists (R1) and SyncTeX answers for a build that is no longer shown.
- SyncTeX is refused while the source has unsaved changes or the build is historical, because the positions would belong to other text.
- CSP stays `script-src 'self'; style-src 'self'`. CodeMirror's style module is mounted as a constructed stylesheet; validators stay generated and need no evaluation.
- API-key execution is listed but never selected automatically. Keel Runtime is not shown because it does not run.

## Evidence vocabulary

已验证, 验证失败, 尚未检查, 结果不确定 and 需要人工审阅 apply separately to: model finished, file applied, LaTeX compiled, structure check, mathematical tool, literature metadata, and reviewed conclusion. A compile never satisfies the mathematics row. The reviewed-conclusion row always stays 需要人工审阅: no control records a human conclusion yet, and none pretends to.

## Backend added for the frontend

`GET /api/builds/{id}/synctex` now returns typed `SyncTexPosition` boxes, and `/api/builds/{id}/synctex/inverse` maps a PDF point to a source file and line, including `\input` files. Both are in `contracts/v1/schema.json` and bound to build identity.

## Acceptance

`npm run accept:web -- --url http://127.0.0.1:<port> --provider codex` drives a running isolated service in system Edge. `--no-model` skips the model step, which is then recorded as not run. It writes `reports/frontend-acceptance.json` and the screenshots in `reports/frontend/`. `scripts/capture_native.ps1` drives the real GPUI window through UI Automation and writes `reports/frontend/native-check.json`.

Results on 2026-10-30:

| Check | Result |
| --- | --- |
| Browser acceptance with one real Codex subscription run on a model chosen in the picker | 29 of 29 |
| axe: source, review, workflow, evidence, dark theme | 0 violations |
| Muted text on the dark drawer over a white PDF page, from composited pixels | 6.11:1 |
| Keystroke to paint on a 236 KB document | median 13 ms, p95 15 ms, max 19 ms |
| Build plus first PDF page | 7.2 s, mostly Docker and TeX |
| `web/app.js` | 936 KB, plus lazy chunks for PDF.js |
| React/jsdom timing, model and message-coverage tests | 58 |
| Python and functional tests, with real Docker compile tests | 242 |
| Rust Diff tests / GPUI native tests | 7 / 4 |

The real loop was: edit, compile, Codex analysis on the model chosen in the picker, proposal, human approval, file apply, independent verification, new PDF containing the approved word.

## Known limits

- Saving is explicit. Unsaved drafts survive file switches within a tab, not a reload.
- Each SyncTeX query starts a bounded container, so it is slower than an in-process lookup.
- Chinese input was accepted through Chromium composition events, not a physical Windows IME session.
- Browser acceptance ran headless. No screen reader was used; axe and keyboard reachability do not replace one.
- PDF.js standard fonts, CMaps and WASM decoders are not bundled, so a PDF that relies on non-embedded fonts or JPX images may render with substitutions. pdfLaTeX output with embedded fonts is what was accepted.
- Chromium's own editing commands tried to copy a `style` attribute onto lines created by multi-line `insertText`; a bare contenteditable under the same CSP reproduces it with no CodeMirror and no script write. The editor now takes that input path itself, and acceptance fails on any violation.
- Only Codex ran in the real loop. Claude Code login state was read, but this acceptance did not run it.
- Crossref results depend on the live service.
- The native 打开论文工作台 button was not clicked, to avoid taking over the user's browser. The URL it opens was tested in the browser.
- English covers the interface. Errors the Python API raises stay in English in both languages, and model answers, file names and LaTeX logs stay as written. Python error text is not yet translated into Chinese.
- Dates and numbers are not locale-formatted. Only Chinese and English exist.
- Styled `<select>` menus need `appearance: base-select` (current Chromium and Edge); other browsers show native menus, which are not frosted.
- Mica needs Windows 11. Older systems show the window's plain tint, which was not tested here.
- There is no phone layout; the workbench is for desktop windows.

Final visual and layout acceptance is separate: `node scripts/accept_final_ui.mjs --url http://127.0.0.1:<port>` writes `reports/final-ui-acceptance.json` and `reports/final-ui/`, and lists what was not run.

This is frontend work under review, not a release or promotional acceptance.
