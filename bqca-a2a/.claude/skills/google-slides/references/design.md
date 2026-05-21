# Designing Google Slides

Don't ship boring slides. A wall of 12pt black text on a white background says "I generated this in 30 seconds and didn't care." Pick a palette, commit to a motif, vary your layouts. The Slides API will render whatever you tell it — taste is on you.

This file is the design playbook. For the API mechanics that implement these ideas (createShape, updateTextStyle, colors, transforms), see [creating.md](creating.md).

---

## Before You Start

- **Pick a bold, content-informed color palette.** The palette should feel designed for *this* topic. If you could swap your colors into a completely different deck and it would still "work," you haven't made specific enough choices.
- **Dominance over equality.** One color should dominate (60–70% visual weight), with 1–2 supporting tones and one sharp accent. Never give all colors equal weight.
- **Dark/light contrast.** Dark backgrounds for title + conclusion slides, light for content (the "sandwich" structure). Or commit to dark throughout for a premium feel.
- **Commit to a visual motif.** Pick ONE distinctive element and repeat it on every slide — rounded image frames, icons in colored circles, a thick single-side border, a consistent corner accent. Repetition is what makes a deck feel designed.

---

## Color Palettes

Pick a palette that matches the topic — don't default to generic blue. Below are starting points; tweak hues to fit your subject.

| Theme | Primary | Secondary | Accent |
|-------|---------|-----------|--------|
| **Midnight Executive** | `1E2761` (navy) | `CADCFC` (ice blue) | `FFFFFF` (white) |
| **Forest & Moss** | `2C5F2D` (forest) | `97BC62` (moss) | `F5F5F5` (cream) |
| **Coral Energy** | `F96167` (coral) | `F9E795` (gold) | `2F3C7E` (navy) |
| **Warm Terracotta** | `B85042` (terracotta) | `E7E8D1` (sand) | `A7BEAE` (sage) |
| **Ocean Gradient** | `065A82` (deep blue) | `1C7293` (teal) | `21295C` (midnight) |
| **Charcoal Minimal** | `36454F` (charcoal) | `F2F2F2` (off-white) | `212121` (black) |
| **Teal Trust** | `028090` (teal) | `00A896` (seafoam) | `02C39A` (mint) |
| **Berry & Cream** | `6D2E46` (berry) | `A26769` (dusty rose) | `ECE2D0` (cream) |
| **Sage Calm** | `84B59F` (sage) | `69A297` (eucalyptus) | `50808E` (slate) |
| **Cherry Bold** | `990011` (cherry) | `FCF6F5` (off-white) | `2F3C7E` (navy) |

### Hex → Slides `rgbColor`

The Slides API uses 0.0–1.0 floats, not 0–255 ints. Convert each channel with `value / 255`.

```python
def hex_to_rgb(h):
    h = h.lstrip("#")
    return {"red":   int(h[0:2], 16) / 255,
            "green": int(h[2:4], 16) / 255,
            "blue":  int(h[4:6], 16) / 255}

# Example
{"rgbColor": hex_to_rgb("1E2761")}
# → {"rgbColor": {"red": 0.118, "green": 0.153, "blue": 0.380}}
```

Wrap it in a `solidFill` or `opaqueColor` as the API expects (see [creating.md](creating.md) for `updateTextStyle` / `updateShapeProperties` examples).

---

## Typography

Pick a font pairing with personality. Both fonts below ship with Google Workspace, so they render correctly when shared.

| Header Font | Body Font | Mood |
|-------------|-----------|------|
| Playfair Display | Lato | Editorial, premium |
| Roboto Slab | Roboto | Modern, technical |
| Merriweather | Open Sans | Long-form, readable |
| Oswald | Roboto | Bold, impactful |
| Google Sans | Roboto | Default Google look |
| Lora | Source Sans Pro | Warm, approachable |
| Montserrat | Lora | Geometric + serif contrast |
| Archivo Black | Inter | Editorial display |
| Bebas Neue | Open Sans | Sports / energetic |
| Source Code Pro | Inter | Engineering, dev tools |

Set fonts via `updateTextStyle` with `"fontFamily": "Roboto Slab"`. The name must match Google Fonts' listing exactly — case and spaces matter.

### Sizes

| Element | Size |
|---------|------|
| Slide title | 36–44pt bold |
| Section header | 20–24pt bold |
| Body text | 14–16pt |
| Captions / footnotes | 10–12pt, muted color (~60% opacity vs. body) |
| Stat callouts (the big number) | 60–96pt |

Match weights to function: titles bold or black, body regular, captions light or italic. Don't bold everything — bold loses meaning when it's everywhere.

---

## Layout Options

**Every slide needs a visual element** — image, chart, icon, or shape. Text-only slides are forgettable.

- **Two-column** — text left, illustration / data / image right (or reversed). Easy default.
- **Icon + text rows** — icon in colored circle, bold header, description below. Use for "3 reasons", "4 principles", etc.
- **2×2 or 2×3 grid** — image on one side, grid of content blocks on the other. Use for capability cards, feature comparisons.
- **Half-bleed image** — full image on one side (left or right), content overlay on the other.
- **Big stat** — one giant number (60–96pt) with a small label below. Use sparingly, for moments that need impact.
- **Quote slide** — large quoted text centered, attribution small below. Great as a section break.
- **Timeline / process** — numbered steps with arrows or connecting lines. Horizontal for short, vertical for long.

### Build with `BLANK` layouts

For consistent visual control, use `slideLayoutReference: {predefinedLayout: "BLANK"}` and place every shape yourself. Other predefined layouts inject placeholder shapes whose styles fight whatever you try to set. (See [creating.md](creating.md) for the API patterns.)

### Repeat a layout — abstract it

If you're building six "capability cards", write one Python helper that takes `(slide_id, x, y, w, h, color, title, body)` and returns the list of `createShape` + `insertText` + `updateTextStyle` + `updateShapeProperties` requests. Call it six times. Never paste six near-copies of the same JSON — they'll drift.

---

## Spacing

- **Slide margins**: 0.5" minimum (36pt) on all sides. The default page is 10" × 5.625" widescreen (720pt × 405pt), so usable area is 648pt × 333pt.
- **Between content blocks**: 0.3–0.5" (22–36pt). Pick one and use it consistently — mixed gaps look sloppy.
- **Around text inside shapes**: text boxes have built-in padding (~7pt). If you're aligning a line or icon with text edges, either set `margin: 0` on the text box or offset by the padding.
- **Breathing room**: leave whitespace. Don't fill every pixel — empty space is what makes the filled space pop.

---

## Avoid (Common Mistakes)

- **Don't repeat the same layout slide after slide.** Vary columns, cards, grids, callouts. Same layout 10× signals laziness.
- **Don't center body text.** Left-align paragraphs and lists; center only titles and short quotes.
- **Don't skimp on size contrast.** A 36pt title above 14pt body is the minimum — anything closer reads flat.
- **Don't default to blue.** Pick colors that reflect the actual topic.
- **Don't mix spacing randomly.** Choose 0.3" or 0.5" gaps and use that gap throughout. Don't switch mid-deck.
- **Don't style one slide and leave the rest plain.** Either commit to a design and apply it everywhere, or stay minimal everywhere.
- **Don't create text-only slides.** Add an image, icon, chart, or shape. Plain title + bullets is forgettable.
- **Don't forget text-box padding when aligning shapes.** Set `margin: 0` or offset the shape — otherwise lines and underlines look off by a few pt.
- **Don't use low-contrast text or icons.** Light text on light background, dark icons on dark background — both unreadable. Use white circles behind dark icons if you need them on a dark background.
- **NEVER use accent lines under titles.** That horizontal-rule-under-the-title pattern is the single biggest AI-deck giveaway. Use whitespace or a background color instead.
- **Don't use predefined layouts (other than `BLANK`) if you want consistent design.** Placeholders fight you. Build everything yourself on `BLANK`.
- **Don't trust the rendered preview in your head.** Always export thumbnails and look — see [qa.md](qa.md).
