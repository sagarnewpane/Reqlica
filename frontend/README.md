Reqlica's local management UI, built with Next.js Pages Router, React, and CSS modules.

## Code organization

```text
src/
  pages/                 Route entry points and server-side API proxies
  components/
    chat/                Chat UI and saved-conversation lifecycle hook
    drafts/              Draft workspace and its styles
    layout/              Dashboard shell and project navigation
    projects/            Project management actions
    requests/            Live request tester
    runtime/             Environment, request headers, and package panels
    settings/            Preferences and model profiles
    ui/                  Shared UI adapters
      shadcn/            Generated primitives (excluded from formatting)
  hooks/                 Shared browser and hydration hooks
  lib/                   Request helpers, route parsing, and browser stores
  styles/                Global theme and color tokens
```

Keep route-specific project selection and loading gates in `pages/`. Put feature
UI and its CSS modules together under `components/`. Cross-feature helpers belong
in `lib/`; reusable React lifecycle logic belongs in `hooks/`. The conversation
hook stays next to chat because its retry/pause behavior is feature-specific.

Use `useHydrated` before reading browser-only state in route output. Keep API
credentials in memory, preserve revision checks on writes, and never automatically
retry an operation whose response was not confirmed.

## Development checks

Run from `frontend/`:

```bash
npm run format        # Apply consistent formatting
npm run format:check  # Check formatting without changing files
npm run lint
npm run typecheck
npm test              # Frontend helper and API proxy regression tests
npm run build
```

Formatting deliberately leaves generated shadcn primitives and Next.js-generated
files alone. Lint and TypeScript still check the UI primitives.

## Getting Started

First, run the development server:

```bash
npm run dev
# or
yarn dev
# or
pnpm dev
# or
bun dev
```

Open [http://localhost:3000](http://localhost:3000) with your browser to see the result.

You can start editing the page by modifying `pages/index.tsx`. The page auto-updates as you edit the file.

[API routes](https://nextjs.org/docs/pages/building-your-application/routing/api-routes) can be accessed on [http://localhost:3000/api/hello](http://localhost:3000/api/hello). This endpoint can be edited in `pages/api/hello.ts`.

The `pages/api` directory is mapped to `/api/*`. Files in this directory are treated as [API routes](https://nextjs.org/docs/pages/building-your-application/routing/api-routes) instead of React pages.

This project uses [`next/font`](https://nextjs.org/docs/pages/building-your-application/optimizing/fonts) to automatically optimize and load [Geist](https://vercel.com/font), a new font family for Vercel.

## Learn More

To learn more about Next.js, take a look at the following resources:

- [Next.js Documentation](https://nextjs.org/docs) - learn about Next.js features and API.
- [Learn Next.js](https://nextjs.org/learn-pages-router) - an interactive Next.js tutorial.

You can check out [the Next.js GitHub repository](https://github.com/vercel/next.js) - your feedback and contributions are welcome!

## Deploy on Vercel

The easiest way to deploy your Next.js app is to use the [Vercel Platform](https://vercel.com/new?utm_medium=default-template&filter=next.js&utm_source=create-next-app&utm_campaign=create-next-app-readme) from the creators of Next.js.

Check out our [Next.js deployment documentation](https://nextjs.org/docs/pages/building-your-application/deploying) for more details.

## Project workspace

The Projects page loads and creates projects through the Reqlica runtime. Start
that backend with `uv run python -m reqlica.runtime.app` from the repository root.
The frontend API defaults to `http://127.0.0.1:4000`; set `REQLICA_RUNTIME_URL` in
`frontend/.env.local` and restart Next.js if the runtime uses another address.
Keep the frontend and backend bound to loopback: these local management APIs do
not provide authentication. Project directories are paths on the runtime host.

Project search, status filters, refresh, creation, and endpoint summaries use live
data. Published endpoints, request logs, and sample-data previews also use live
management APIs rather than placeholder records.

## Sample data

**Sample data** supports three explicit workflows:

- **JSON fixture**: edit and validate a bounded record array, then download JSON.
  This does not write project files or wire fixtures into an endpoint.
- **Existing SQLite table**: explicitly load current database definitions, choose
  an ordinary supported table, edit records, and validate on a disposable copy.
- **Related database records**: provide an object mapping up to 10 existing table
  names to record arrays. References such as `{"$ref":"users.0.id"}` follow declared
  foreign keys; preview IDs are provisional and actual IDs resolve on insertion.

The screen never reads existing database rows or invents example fields. Use
project chat to prepare records and paste its JSON into the editor if desired;
opening chat makes no model request. The old fake record browser and nonfunctional
edit/delete/reset controls were removed because the backend does not offer generic
CRUD for existing rows. Use the project's API to inspect or modify live records.

Only a passed, current SQLite/related preview enables **Insert reviewed records…**.
Confirm the destination table counts, exact preview digest, and schema fingerprint.
Insertion is an atomic append; failures roll back the transaction rather than
overwriting existing rows. The backend rechecks constraints and application code.
Editing records, changing targets/tables, or reloading the schema invalidates the
preview. Success or failure consumes the insertion review; another insert requires
fresh validation. Inserts are not idempotent: after an unconfirmed response, check
live data and acknowledge that check before preparing another validation.

Limits: 100 total records, 128 KiB of JSON input, 50 fields per object, six nested
levels, and 8 KiB strings. The browser rejects unsafe integer values instead of
silently rounding large IDs. Tables with triggers/virtual storage and unsupported
application contracts are refused. Records stay in page memory only; download
them before refreshing or navigating away. There is no automatic retry or undo.

## Request logs and capture

**Request logs** reads saved events for the selected project from the console.
Filter by event kind or exact request ID; use **Older events** for the next page
and **Newest events** or **Refresh history** to return to the latest evidence.
Each page contains up to 50 events. Details are fetched separately, and related
request/error events can be found using **Show related request events**.

History is best-effort, retains up to 1,000 combined events per project, and is
not a live stream or an audit log. Events can disappear while browsing due to
retention. Reading history never starts workers, executes handlers, or replays
requests. Management calls and disposable draft checks are not recorded.

**Capture settings** loads the project's saved preferences without changing them.
Request/response bodies and stdout/stderr capture both default to off. Review and
explicitly confirm any change before saving; updates carry the exact loaded
settings revision and apply immediately. Body previews are limited to 128–2,048
bytes; output is limited to 2 KiB per write. Omission/truncation is shown in event
details. Captured text is displayed as text, not executable HTML.

Redaction is best-effort, and captured evidence is available to local management
tools and Project AI. Use only appropriate local test data. Disabling capture
does not erase retained evidence. After an unconfirmed/conflicting write, cancel
the dialog and **Reload capture settings** before saving again; no automatic
write retries occur.

## Published endpoints

The **Endpoints** page reads the selected project's real published manifest from
the runtime. Search matches method, path, and Python handler; a method filter and
route selection drive the details panel. Refresh reloads the current routes and
worker status after publication. For stopped/idle projects the page explicitly
shows configured routes, not a claim that they are running.

**Test endpoint** opens the project-scoped request tester with the exact method
and route prefilled. It never sends a request automatically. Replace path
parameters with actual values and review non-read operations before sending.
TRACE routes are listed for inspection but are not supported by the bounded
request proxy. Removed or unsupported deep links show an error rather than
silently selecting a different route.

To add or change endpoints, use project chat and the draft workspace. The page
does not fabricate descriptions or sample responses, or directly edit live routes.

## Project chat

Open a project by its name or **Open project**, then use **Project chat**. The
workspace also displays that project's published endpoints. Conversations are
stored by the console in the project; refresh or reopen the project to resume.

Run the console on a separate port from Next.js:

```bash
CONSOLE_PORT=3001 uv run python -m reqlica.console.app
```

`REQLICA_CONSOLE_URL` defaults to `http://127.0.0.1:3001`. Override it in
`frontend/.env.local` if needed. The runtime remains on port 4000.
Choose a provider and a tool-capable model ID under **Model & permissions**.
Enter a provider key once in the project's **Settings → AI connection → API key**
and click **Save key**. Model discovery and chat reuse it automatically, including
after refresh or restart. **Remove key** deletes it; entering another key replaces it.
Local Ollama does not require a key. The selected provider receives project context.

Keys are scoped to the project, provider, and provider URL. The console keeps them
in `$REQLICA_HOME/provider-keys/<project-id>.json` (default `~/.reqlica/provider-keys/`),
with owner-only file/directory permissions (`0600`/`0700`). These are local plaintext
secrets protected by filesystem permissions, not encryption. They stay outside
project source, drafts, exports, browser storage, model input, and saved conversations.
The status API reports only whether a key is configured. The explicit **Show key**
action returns its value to the current page without caching it; **Hide key** clears
the displayed value. Protect your local account and backups. The console's environment key remains a fallback
when no project key is configured.

Chat advances one saved backend step at a time. Pause takes effect after the
current step completes; navigating away prevents further steps. An unconfirmed
step is refreshed, not automatically replayed. Draft edits and code execution
are separate permissions. This screen does not publish drafts.

## Project workers

Worker controls are available in the project workspace and under
**Runtime & requests → Worker**. **Refresh status** is read-only and does not
start a worker. Start and stop each require confirmation showing the exact
project. Starting executes trusted published Python code; stopping can interrupt
requests but keeps source, database records, drafts, and conversations.

`idle` means the next live API request can start a worker automatically. `stopped`
means live requests return 503 until an explicit start for this gateway session;
restarting the gateway resets that stopped state. Use stop then start to load
updated environment variables. Process IDs and loaded environment revisions are
shown only when reported by the runtime. Status is a snapshot, not a health stream.

Actions update the workspace's project metadata without remounting chat. An
unconfirmed/failed action marks the status unverified and disables further worker
changes until a successful status refresh. Lifecycle requests are never retried
automatically, and provider credentials are not forwarded.

## Model discovery and runtime configuration

Settings and chat use a searchable Radix Popover/cmdk model picker. Model catalogs
come from the console's project-scoped discovery endpoint, using the project's
saved provider key on the backend. A manual model ID remains available
when discovery is unavailable. Catalog membership does not verify tool support.

Inside a project, **Runtime & requests** contains:

- Environment: write-only variables backed by the console, with revision checks.
  Restart the worker after a change.
- Request headers: project-scoped defaults held only in SPA memory. Browser
  refresh clears them. They are sent only by an explicit request-tester action.
- Python packages: shared installed inventory and a resolve/review/confirm flow.
  Installation affects the shared environment; it never runs on page load.

The request tester sends real project-scoped requests through a bounded proxy.
It does not follow redirects and limits response previews to 1 MiB. Testing
non-read methods can modify live project data.

## Model profiles

Settings → AI connection supports up to 30 named model profiles per browser.
Create, rename/update, duplicate, or delete profiles. A profile contains provider,
model ID, base URL, response token limit, and request timeout. API keys and project
permissions are excluded. Selecting a profile loads it into the settings form;
**Save changes** makes that configuration the workspace/project default. New chats
can choose any saved profile under **Model & permissions**. Selecting a different
profile selects its matching saved project key and preserves edit/test permissions.
Existing conversations retain their original model configuration.

## Shared UI patterns and legacy parity

Use `components/ui/PageHeader/PageHeader` for page titles, metadata, and adjacent
actions. `WorkerControls` supplies the shared title/status/PID/Start/Stop/Refresh
header for Project chat, Endpoints, and Runtime. Other screens use the same header
without unrelated runtime controls.

Use `components/ui/Button/Button` with `primary`, `secondary`, `ghost`, or `danger`.
Button links use `asChild` with `Link`, rather than a nested button and link.
Existing native actions compose the same CSS-module variants. Keep the shared
44px controls, 24px page titles, page gutters, and confirmation button styling.

See [`docs/frontend-parity-audit.md`](../docs/frontend-parity-audit.md) for the
comparison with `backend/reqlica/reqlicav1.html`, missing workflows, backend
references, and recommended implementation order. Audited gaps are not implemented
by the UI consistency pass.

## Sidebar components

Project navigation uses the official shadcn/ui Radix sidebar, installed with
`shadcn add sidebar` into `src/components/ui/shadcn/`. Its desktop panel stays
fixed while page content scrolls; mobile uses the library's modal Sheet drawer.
The app-level SidebarProvider preserves collapse state between page transitions.
Project scope is resolved from the concrete URL as well as the router query.

The generated mobile hook uses useSyncExternalStore for hydration-safe viewport
subscriptions. Skeleton width is deterministic for React purity checks. Reqlica
colors are applied through the sidebar theme tokens in globals.css. Component
aliases are recorded in components.json for subsequent shadcn additions.

Shared cards, inputs, buttons, badges, and select controls now wrap the official
shadcn components. The Select adapter keeps option-based call sites while using
Radix Select's keyboard navigation, portals, and focus management; call sites use
onValueChange rather than synthetic DOM change events. Empty custom/new choices
are explicitly mapped to a private nonempty item value for Radix compatibility.
Project cards use shadcn DropdownMenu for scoped navigation and Skeleton for
loading. Deleting a model profile uses shadcn AlertDialog with explicit confirmation.

## Project and draft lifecycle

Project-card actions include removal with a server-side directory preview. By
default only the registration is removed and the worker stopped. Deleting files
requires a separate checkbox, typing the project name, and forwarding the exact
previewed directory. Backend protection of shared/protected directories remains
in effect. Unconfirmed responses are not retried automatically.

Draft workspace uses live console data: create, inspect changed files, rename,
delete, validate, and test. Every mutation of an existing draft carries its
inspected revision. Deletion and code-executing checks require explicit dialog
confirmation. Chat links directly to its draft. Publishing remains a separate
workflow.

**Draft source files** lets you search/browse the saved file list, inspect UTF-8
source, edit existing files, and create a new project-relative source file. Saving
is explicit and carries the revision returned when that file was loaded; it does
not execute code or change the published API. The backend clears validation,
tests, and approval after a save. Rerun checks before approving the new revision.
Editing a manifest/source file does not automatically make it valid.

Unsaved edits block draft switching, refresh, checks, and publication until saved
or explicitly discarded. File switching/reloading asks before discarding local
text; link/history navigation and browser unload are guarded too. Local text can
be downloaded before recovery. On a save conflict or lost acknowledgement, the
editor preserves the text and blocks another save: keep/download it, explicitly
discard local edits, then refresh the workspace to inspect saved state. No writes
are automatically replayed. This editor supports source up to 1 MiB; use local
tools for larger, binary, or non-UTF-8 files. Private control/database paths and
path traversal are rejected, and symlinks remain guarded by the backend. File
deletion is not offered by this editor.

Under **Review & publish**, passing validation and at least one passing test must
match the exact draft revision. **Approve revision…** records an explicitly
confirmed review without changing the live API. **Activate approved revision…**
is a separate confirmation showing the full revision and base version; it publishes
the code through the runtime and can briefly interrupt requests. Candidate startup
runs trusted Python code with access to live data and the runtime machine.

Both actions require the draft base to match the current published version.
The backend rechecks code, environment, dependencies, approval, and database
readiness. Required database setup must currently be completed using the backend
management tools. After a failed/unconfirmed publication response, cancel the
dialog and use **Refresh** before another attempt; writes are never retried
automatically. After activation, the saved draft remains based on its old version;
create a new draft for further changes.

The brand mark is a dedicated SVG, replacing the cropped raster draft so it
stays sharp and contains no wordmark fragments at different sizes.
