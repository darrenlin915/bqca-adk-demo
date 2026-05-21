# QA for Google Slides

**Assume there are problems. Your job is to find them.**

Your first render is almost never correct. Approach QA as a bug hunt, not a confirmation step. If you found zero issues on first inspection, you weren't looking hard enough.

This applies even to "I just changed one line" — `replaceAllText` matched somewhere you didn't expect, the font fell back because the name was misspelled, the new text wraps and now overflows the box. Look.

---

## Content QA

Dump the deck to markdown and grep for the obvious problems.

```bash
# Full text dump (includes speaker notes)
python scripts/extract_text.py <presentationId-or-url>

# Hunt for leftover placeholders
python scripts/extract_text.py <pid> | grep -iE "\{\{|xxxx|lorem|ipsum|TODO|FIXME|placeholder|this.*(page|slide).*layout"
```

If grep returns anything, fix it before declaring success. `{{` is especially insidious — a single forgotten `{{date}}` looks fine in the editor preview but screams when the deck is shared.

Also check:

- **Typos and casing** — names, product titles, capitalization style.
- **Order** — did you create slides in the right `insertionIndex`? Off-by-one is easy.
- **Numbers and facts** — if you wrote any stats, did you copy them correctly from the source?
- **Speaker notes** — did you generate notes for every slide that needs them, or did some fall through?

---

## Visual QA

Text dump catches content bugs. It does not catch overlapping shapes, text overflow, low contrast, off-slide elements, or an accent line you forgot to delete. For those you need to see the deck.

### Step 1: Export thumbnails

```bash
# All slides → ./slide-01.png, ./slide-02.png, ...
python scripts/thumbnails.py <presentationId-or-url>

# Specific slides only (1-based)
python scripts/thumbnails.py <pid> --slides 3,5,7 -o ./out/
```

The script uses `presentations.pages().getThumbnail()` — same image the user sees in the Slides editor. No LibreOffice / PDF dance needed.

### Step 2: Use a subagent

**You've been staring at the code and will see what you expect, not what's there.** Subagents have fresh eyes. Even for a 3-slide deck.

Hand the images to a vision-capable subagent with this prompt (adapt the slide list):

```
Visually inspect these slides. Assume there are issues — find them.

Look for:
- Overlapping elements (text through shapes, lines through words, stacked text)
- Text overflow or cut off at edges / box boundaries
- Decorative lines positioned for single-line text but title wrapped to two lines
- Source citations or footers colliding with content above
- Elements too close (< 0.3" / 22pt gaps) or cards/sections nearly touching
- Uneven gaps (large empty area on one side, cramped on the other)
- Insufficient margin from slide edges (< 0.5" / 36pt)
- Columns or repeated elements not aligned consistently
- Low-contrast text (e.g., light gray text on cream background)
- Low-contrast icons (e.g., dark icons on dark background without a contrasting circle)
- Text boxes too narrow causing excessive wrapping
- Leftover placeholder content
- Accent lines under titles (AI-generated giveaway — remove)

For each slide, list issues or areas of concern, even if minor.

Read and analyze these images:
1. /abs/path/slide-01.png  (Expected: title slide — "Q2 OKR Review")
2. /abs/path/slide-02.png  (Expected: highlights — 3 bullets, sage palette)
3. /abs/path/slide-03.png  (Expected: big stat — "+42%")

Report ALL issues found, including minor ones.
```

The "Expected" line per slide matters — without it the agent has no way to know whether what they see is what was intended.

---

## Verification Loop

1. Generate → export thumbnails → inspect (with subagent)
2. **List issues found.** If none, look again more critically — or hand to a *different* subagent.
3. Fix issues via `batchUpdate` (`updatePageElementTransform` to move, `deleteObject` + `createShape` to rebuild, etc.). See [editing.md](editing.md).
4. **Re-export thumbnails for the affected slides only** — `thumbnails.py --slides 3,7`.
5. **Re-verify.** One fix often creates another problem (you moved a title, now the underline doesn't match; you shrank a box, now text wraps).
6. Repeat until a full pass reveals no new issues.

**Do not declare success until you've completed at least one full fix-and-verify cycle.**

For converted .pptx files (via `upload_pptx.py`), the first cycle almost always finds drift — fonts substituted, custom shapes flattened, animations dropped. Spot-check at minimum the first three and last three slides, plus any slide with custom graphics.

---

## When the deck is for the user, not by them

If the user gave you a `presentationId` to edit and you've made changes, send them the URL and a short "things I changed" summary. Don't wait for them to discover it — proactively flag anything you couldn't verify ("the chart on slide 4 came in from a PPTX upload, so it may have shifted — please eyeball it").
