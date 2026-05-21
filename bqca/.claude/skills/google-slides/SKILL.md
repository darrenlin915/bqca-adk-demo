---
name: google-slides
description: "Use this skill any time Google Slides is involved in any way — as input, output, or both. This includes: creating new presentations programmatically via the Google Slides API; reading, parsing, or extracting text/structure from any Google Slides URL or presentationId (even if the extracted content will be used elsewhere, like in an email or summary); editing or updating slides via batchUpdate; converting local .pptx files into Google Slides format via Drive upload; working with speaker notes, layouts, or shape positioning. Trigger whenever the user mentions \"Google Slides\", \"docs.google.com/presentation\", \"GSlides\", a presentationId, or asks to upload/convert a .pptx to Drive. If a Google Slides presentation needs to be opened, created, edited, read, or converted, use this skill."
---

# Google Slides Skill

## Quick Reference

| Task | Guide |
|------|-------|
| Set up auth (do this first) | [Auth section below](#authentication) |
| Read/extract content | `python scripts/extract_text.py <presentationId-or-url>` |
| Create from scratch | Read [references/creating.md](references/creating.md) |
| Edit existing presentation | Read [references/editing.md](references/editing.md) |
| Apply design guidance | Read [references/design.md](references/design.md) |
| Visual QA (subagent inspect) | `python scripts/thumbnails.py <pid>` + read [references/qa.md](references/qa.md) |
| Convert .pptx → Google Slides | `python scripts/upload_pptx.py <file.pptx>` |

---

## In this project

This skill is shipped inside the `bqca-agent` ADK project. There are **two layers** of Google Slides code here — don't confuse them:

| Layer | Where | When to use |
|-------|-------|-------------|
| **Runtime tool** (called by the agent at request time) | `app/slides.py` → `create_bqca_report(title, tool_context)` | The agent invokes this as a `FunctionTool` to turn the most recent BQCA result (`state["last_bqca_payload"]`) into a deck. End-user OAuth is negotiated via `app.tools._negotiate_creds` — do **not** swap in `scripts/auth.py` here. |
| **Skill scripts** (called by *you* at dev time, from this terminal) | `scripts/extract_text.py`, `scripts/thumbnails.py`, `scripts/upload_pptx.py` | Inspecting / QA'ing decks the agent produced, prototyping new layouts, debugging a presentation by ID. Auth comes from `scripts/auth.py` (ADC / service account / cached OAuth) — independent of the agent's user-token flow. |

### When editing `app/slides.py`

- It uses **EMU** units (`SLIDE_WIDTH_EMU = 9144000`, etc.) and pre-built helpers (`_text_box`, `_table_requests`, `_build_slide_requests`). The Slides API patterns in [references/creating.md](references/creating.md) and [references/editing.md](references/editing.md) all apply — just note the unit choice and reuse the existing helpers instead of inlining new `createShape` JSON.
- `objectId`s come from `_oid(prefix)` (UUID-suffixed). Keep that — Slides requires `[a-zA-Z0-9_]`, 5–50 chars.
- After every `batchUpdate`, the tool re-fetches the presentation to discover the final page IDs, then calls `presentations.pages().getThumbnail(...)` to inline MEDIUM PNGs into the chat response. Thumbnails are **base64-encoded** before returning (raw `bytes` breaks ADK's JSON history serialization). Preserve that contract.
- Visual changes to the runtime output? Run a real BQCA query through `agents-cli playground`, take the returned `presentation_id`, then `python .claude/skills/google-slides/scripts/thumbnails.py <pid> -o /tmp/qa/` and inspect per [references/qa.md](references/qa.md).

### When the agent is broken vs. when Slides is broken

If `create_bqca_report` returns `{"status": "ERROR", ...}`, the error often isn't from Slides — check `_negotiate_creds` first (OAuth scope drift, missing `USE_ADC` env, expired token). Reach for the Slides API references only after you've confirmed the credentials object is actually valid.

> Note: `app/slides.py` is identical between `bqca-agent` and `bqca-agent-a2a`. Fixes to slide construction should land in both unless one variant explicitly diverges.

---

## Why a script-heavy skill

Google Slides is a REST API. Every operation = an HTTP call with a structured JSON body. Writing those calls by hand is tedious and the failure modes are subtle (wrong scopes, missing `objectId`, EMU unit confusion). Bundled scripts handle the common cases correctly so you don't have to rediscover the gotchas every time.

For anything more involved than the bundled scripts cover (custom layouts, complex batchUpdate sequences), read the relevant `references/*.md` file — they distill the patterns that work and the ones that don't.

---

## Authentication

The Slides API needs OAuth-style credentials with the right scopes. Three sources are supported, checked in this order:

1. **`GOOGLE_APPLICATION_CREDENTIALS`** env var → service account JSON. Best for headless / CI / shared environments.
2. **Application Default Credentials (ADC)** → from `gcloud auth application-default login`. Best for personal use on a machine where `gcloud` is already set up.
3. **Cached OAuth token** at `~/.config/google-slides-skill/token.json` → for personal Google accounts when ADC isn't available. First run will open a browser if `client_secret.json` exists at `~/.config/google-slides-skill/client_secret.json`.

**Required scopes** (the helper requests these automatically):
- `https://www.googleapis.com/auth/presentations` — read/write Slides
- `https://www.googleapis.com/auth/drive.file` — needed for PPTX upload + thumbnail export

### Quick setup (most common path)

If `gcloud` is installed and the user has a personal Google account:

```bash
gcloud auth application-default login \
  --scopes="https://www.googleapis.com/auth/presentations,https://www.googleapis.com/auth/drive.file,openid,https://www.googleapis.com/auth/userinfo.email"
```

ADC will then work for the rest of the session.

### Verifying auth

Before doing real work, confirm credentials resolve:

```bash
python -c "from scripts.auth import get_credentials; c = get_credentials(); print('✓ auth source:', c.__class__.__module__)"
```

If this fails, **stop and ask the user to set up auth** — don't paper over it. Most issues at runtime trace back to auth misconfiguration.

### Dependency install

Recommended — uv (project or one-shot env):

```bash
# Inside a uv-managed project
uv add google-api-python-client google-auth google-auth-oauthlib google-auth-httplib2

# Or one-shot into the active venv
uv pip install google-api-python-client google-auth google-auth-oauthlib google-auth-httplib2
```

Fallback — plain pip:

```bash
pip install --break-system-packages google-api-python-client google-auth google-auth-oauthlib google-auth-httplib2
```

(Drop `--break-system-packages` if you're already in a venv.)

---

## Reading a Presentation

Quick text extraction:

```bash
python scripts/extract_text.py 1ABC...  # presentationId
python scripts/extract_text.py https://docs.google.com/presentation/d/1ABC.../edit
```

Outputs markdown with one section per slide, including speaker notes. Useful when the user wants a summary or wants the content in a different medium (email, doc, post).

For programmatic access (when you need the full JSON structure):

```python
from googleapiclient.discovery import build
from scripts.auth import get_credentials

service = build('slides', 'v1', credentials=get_credentials())
pres = service.presentations().get(presentationId='1ABC...').execute()
# pres['slides'] is a list of slide objects with pageElements, etc.
```

See [references/reading.md](references/reading.md) for parsing patterns (extracting tables, images, speaker notes, finding a slide by title, etc.).

---

## Creating a Presentation

Read **[references/creating.md](references/creating.md)** for the full pattern. The short version:

1. `presentations.create({'title': ...})` → returns `presentationId`
2. Build a list of `batchUpdate` requests (createSlide / createShape / insertText / updateTextStyle / createImage / ...)
3. `presentations.batchUpdate(presentationId, {'requests': [...]})`

Always assign your own `objectId` strings when creating shapes — that way subsequent requests in the same batch can reference them.

---

## Design

Don't ship boring slides. Read **[references/design.md](references/design.md)** before you build anything non-trivial — pick a palette, commit to a motif, vary layouts. The Slides API will happily render a wall of 12pt Calibri on white; that's on you to avoid.

Key reminders the API won't enforce:

- Use `predefinedLayout: "BLANK"` and place every shape yourself — other layouts inject placeholders that fight your styling.
- Pick a color palette that fits the *topic*, not generic blue.
- Every slide needs a visual element. Text-only slides are forgettable.
- Never put an accent line under a title — biggest AI-deck giveaway.

---

## Editing an Existing Presentation

Read **[references/editing.md](references/editing.md)** for the patterns.

Common operations: replace text (`replaceAllText`), insert a new slide at position N, delete a slide, update shape text/style/position, add an image.

`replaceAllText` is the easiest for templated decks — write the slides once with `{{placeholder}}` strings, then swap them in via batchUpdate.

---

## Converting .pptx → Google Slides

```bash
python scripts/upload_pptx.py /path/to/deck.pptx
# or specify a Drive folder:
python scripts/upload_pptx.py /path/to/deck.pptx --folder-id <driveFolderId>
# or a custom title:
python scripts/upload_pptx.py /path/to/deck.pptx --title "Q2 Review"
```

This uploads to Drive with the Google Slides MIME type, which triggers automatic conversion. Outputs the new presentationId and URL.

For complex .pptx files, conversion fidelity isn't perfect (Slides has fewer layout primitives than PowerPoint). Warn the user that fonts, custom shapes, and animations may shift. If they need pixel-perfect, keep the .pptx — use the `pptx` skill instead.

---

## When to use this skill vs. the `pptx` skill

| Situation | Use |
|-----------|-----|
| File ends in `.pptx`, stays as `.pptx` | `pptx` skill |
| Output needs to live in Google Drive / be shareable via link | `google-slides` |
| User mentions "Google Slides", a `docs.google.com/presentation/...` URL, or a presentationId | `google-slides` |
| Convert .pptx to Google Slides | `google-slides` (`scripts/upload_pptx.py`) |
| Pure local file authoring with no online sharing | `pptx` skill |

If the user is ambiguous (e.g., "build me a deck"), ask whether they want a `.pptx` file or a Google Slides presentation. Default to `.pptx` if they need to email the file; default to Google Slides if they need a shareable link or want to collaborate.

---

## QA

**Assume there are problems. Look hard.** Read **[references/qa.md](references/qa.md)** for the full workflow (subagent visual inspection, fix-and-verify loop).

The short version:

1. **Content** — `python scripts/extract_text.py <pid> | grep -iE "\{\{|xxxx|lorem|TODO"`. Fix any matches.
2. **Visual** — `python scripts/thumbnails.py <pid>` exports each slide as a PNG. Hand them to a subagent and ask it to find issues (overlaps, overflow, low contrast, accent lines under titles, …). Even for 2–3 slides — fresh eyes catch what you won't.
3. **Loop** — fix → re-export the affected slides (`--slides 3,7`) → re-inspect. Repeat until a full pass reveals nothing new. Don't declare success without at least one full fix-and-verify cycle.

For converted .pptx files: drift is common. Spot-check first three and last three slides at minimum.

---

## Common Pitfalls

1. **Wrong scope** — `drive` is too broad, `drive.readonly` won't let you create files. Stick to `presentations` + `drive.file`.

2. **Coordinate units** — the API uses EMU (English Metric Units): 914400 EMU = 1 inch. Or use the `Unit.PT` form: `{'magnitude': 200, 'unit': 'PT'}`.

3. **createShape requires both `elementProperties.size` and `elementProperties.transform`** — omitting either silently produces a zero-size shape that's invisible.

4. **insertText needs the shape to exist first** — if you're creating a shape and adding text in the same batch, list `createShape` before `insertText`. Within a single batchUpdate, requests run in order.

5. **`replaceAllText` is case-sensitive** by default. Use `containsText.matchCase: false` if you don't want that.

6. **`createImage` needs a publicly accessible URL** OR an image already uploaded to Drive. Local file paths don't work — upload first.

7. **Don't forget speaker notes** when extracting text — they live under each slide's `slideProperties.notesPage` (different structure from `pageElements`). The bundled `extract_text.py` handles this correctly.

8. **Rate limits** — bursts of batchUpdate calls can hit quota. If you have many edits, batch them into a single `requests` array (the API takes hundreds of requests per call) instead of making N round trips.

---

## Dependencies

- `google-api-python-client` — Slides + Drive API client
- `google-auth`, `google-auth-oauthlib`, `google-auth-httplib2` — credentials handling
- (optional) `gcloud` CLI — for ADC setup
