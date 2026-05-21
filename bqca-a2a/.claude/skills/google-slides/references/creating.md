# Creating Google Slides from Scratch

The flow is always: **create empty presentation → batchUpdate to add content**.

```python
from googleapiclient.discovery import build
from scripts.auth import get_credentials

slides = build("slides", "v1", credentials=get_credentials())

# 1. Create empty presentation
pres = slides.presentations().create(body={"title": "Q2 OKR Review"}).execute()
pid = pres["presentationId"]

# 2. Add content via batchUpdate
slides.presentations().batchUpdate(
    presentationId=pid,
    body={"requests": [...]}
).execute()

print(f"https://docs.google.com/presentation/d/{pid}/edit")
```

The new presentation comes with a single blank "TITLE" slide. You can use it, replace it, or delete it.

---

## The mental model

Everything on a slide is a "page element" — a shape, image, table, line, or group. You create them, position them, then style them. The Slides API doesn't have a high-level "add a bullet list" call; you build that out of `createShape` (TEXT_BOX) + `insertText` + `updateParagraphStyle`.

Two practical consequences:

1. **Assign your own `objectId`.** When you create a shape, give it a stable string ID. Subsequent requests in the same batch reference it. If you let the API generate IDs, you can't reference them mid-batch.
2. **Order matters within a single batchUpdate.** Requests run top-to-bottom. Create shapes before inserting text into them.

---

## Coordinates and sizing

Two unit options for any size/position:

```python
{"magnitude": 200, "unit": "PT"}   # points (1/72 inch)
{"magnitude": 4000000, "unit": "EMU"}  # English Metric Units (914400 = 1 inch)
```

**Default page size**: 10" × 5.625" widescreen = `{width: 9144000, height: 5143500}` in EMU, or `{width: 720, height: 405}` in PT.

For positioning, use a `transform` with `translateX` / `translateY`:

```python
"transform": {
    "scaleX": 1, "scaleY": 1,
    "translateX": 50, "translateY": 100,
    "unit": "PT"
}
```

---

## Common request types — examples

### createSlide

```python
{
    "createSlide": {
        "objectId": "slide_intro",        # your chosen ID
        "insertionIndex": 0,              # position in deck
        "slideLayoutReference": {
            "predefinedLayout": "BLANK"   # or TITLE, TITLE_AND_BODY, SECTION_HEADER, ...
        }
    }
}
```

Predefined layouts include: `BLANK`, `TITLE`, `TITLE_AND_BODY`, `TITLE_AND_TWO_COLUMNS`, `TITLE_ONLY`, `SECTION_HEADER`, `SECTION_TITLE_AND_DESCRIPTION`, `ONE_COLUMN_TEXT`, `MAIN_POINT`, `BIG_NUMBER`.

**Tip**: For full design control, use `BLANK` and place every element yourself. Predefined layouts inject placeholders that fight your styling.

### createShape (text box)

```python
{
    "createShape": {
        "objectId": "title_box",
        "shapeType": "TEXT_BOX",
        "elementProperties": {
            "pageObjectId": "slide_intro",
            "size": {
                "width":  {"magnitude": 600, "unit": "PT"},
                "height": {"magnitude": 80,  "unit": "PT"}
            },
            "transform": {
                "scaleX": 1, "scaleY": 1,
                "translateX": 60, "translateY": 60,
                "unit": "PT"
            }
        }
    }
}
```

Other shape types: `RECTANGLE`, `ROUND_RECTANGLE`, `ELLIPSE`, `TRIANGLE`, `DIAMOND`, `LEFT_ARROW`, `RIGHT_ARROW`, `STAR_5`, plus dozens more from PowerPoint's set.

### insertText

```python
{
    "insertText": {
        "objectId": "title_box",
        "text": "Q2 OKR Review",
        "insertionIndex": 0
    }
}
```

For multi-paragraph text, just put `\n` in the string.

### updateTextStyle

```python
{
    "updateTextStyle": {
        "objectId": "title_box",
        "textRange": {"type": "ALL"},
        "style": {
            "fontFamily": "Google Sans",
            "fontSize": {"magnitude": 36, "unit": "PT"},
            "bold": True,
            "foregroundColor": {"opaqueColor": {"rgbColor": {
                "red": 0.13, "green": 0.13, "blue": 0.14
            }}}
        },
        "fields": "fontFamily,fontSize,bold,foregroundColor"
    }
}
```

**Important**: `fields` is required and must list every property you set. Comma-separated, no spaces. If you set a property but don't list it in `fields`, the API silently ignores it.

Colors are 0.0–1.0 floats (not 0–255). For hex `#4285F4`: `red=0x42/255=0.259, green=0x85/255=0.522, blue=0xF4/255=0.957`.

### Bullets via createParagraphBullets

```python
# After insertText with newline-separated lines:
{
    "createParagraphBullets": {
        "objectId": "body_box",
        "textRange": {"type": "ALL"},
        "bulletPreset": "BULLET_DISC_CIRCLE_SQUARE"
    }
}
```

Other presets: `BULLET_ARROW_DIAMOND_DISC`, `BULLET_CHECKBOX`, `NUMBERED_DIGIT_ALPHA_ROMAN`, etc.

### updateShapeProperties (fill / outline)

```python
{
    "updateShapeProperties": {
        "objectId": "card_bg",
        "shapeProperties": {
            "shapeBackgroundFill": {
                "solidFill": {
                    "color": {"rgbColor": {"red": 0.26, "green": 0.52, "blue": 0.96}},
                    "alpha": 1.0
                }
            },
            "outline": {"outlineFill": {"solidFill": {"alpha": 0}}}  # transparent border
        },
        "fields": "shapeBackgroundFill.solidFill,outline"
    }
}
```

### createImage

The image must be at a publicly accessible URL OR uploaded to Drive first.

```python
{
    "createImage": {
        "objectId": "hero_img",
        "url": "https://storage.googleapis.com/.../hero.png",
        "elementProperties": {
            "pageObjectId": "slide_intro",
            "size": {
                "width":  {"magnitude": 400, "unit": "PT"},
                "height": {"magnitude": 250, "unit": "PT"}
            },
            "transform": {
                "scaleX": 1, "scaleY": 1,
                "translateX": 280, "translateY": 100,
                "unit": "PT"
            }
        }
    }
}
```

For local images, upload to Drive first (set the file to readable by anyone with the link), then use the `webContentLink` URL.

### updatePageProperties (slide background)

```python
{
    "updatePageProperties": {
        "objectId": "slide_intro",
        "pageProperties": {
            "pageBackgroundFill": {
                "solidFill": {
                    "color": {"rgbColor": {"red": 0.13, "green": 0.13, "blue": 0.14}}
                }
            }
        },
        "fields": "pageBackgroundFill.solidFill"
    }
}
```

### Speaker notes

Each slide has a `notesPage` with a special placeholder. To add notes, find that placeholder's `objectId` from the existing presentation, then `insertText` into it.

```python
# After creating the slide, fetch the notes placeholder ID:
pres = slides.presentations().get(presentationId=pid).execute()
slide = next(s for s in pres["slides"] if s["objectId"] == "slide_intro")
notes_id = slide["slideProperties"]["notesPage"]["notesProperties"]["speakerNotesObjectId"]

# Then:
{
    "insertText": {
        "objectId": notes_id,
        "text": "Speaker notes here.",
        "insertionIndex": 0
    }
}
```

---

## A complete minimal example

A 3-slide deck: title page + two content slides.

```python
from googleapiclient.discovery import build
from scripts.auth import get_credentials

slides = build("slides", "v1", credentials=get_credentials())

# Step 1: create
pres = slides.presentations().create(body={"title": "Demo Deck"}).execute()
pid = pres["presentationId"]
default_slide_id = pres["slides"][0]["objectId"]

# Step 2: build all the slides in one batchUpdate
requests = [
    # Delete the auto-created blank slide
    {"deleteObject": {"objectId": default_slide_id}},

    # Slide 1 — Title
    {"createSlide": {"objectId": "s1", "insertionIndex": 0,
                     "slideLayoutReference": {"predefinedLayout": "BLANK"}}},
    {"updatePageProperties": {
        "objectId": "s1",
        "pageProperties": {"pageBackgroundFill": {"solidFill": {
            "color": {"rgbColor": {"red": 0.13, "green": 0.13, "blue": 0.14}}}}},
        "fields": "pageBackgroundFill.solidFill"}},
    {"createShape": {"objectId": "s1_title", "shapeType": "TEXT_BOX",
                     "elementProperties": {
                         "pageObjectId": "s1",
                         "size": {"width": {"magnitude": 600, "unit": "PT"},
                                  "height": {"magnitude": 100, "unit": "PT"}},
                         "transform": {"scaleX": 1, "scaleY": 1,
                                       "translateX": 60, "translateY": 150, "unit": "PT"}}}},
    {"insertText": {"objectId": "s1_title", "text": "Demo Deck", "insertionIndex": 0}},
    {"updateTextStyle": {
        "objectId": "s1_title", "textRange": {"type": "ALL"},
        "style": {"fontSize": {"magnitude": 56, "unit": "PT"}, "bold": True,
                  "foregroundColor": {"opaqueColor": {"rgbColor": {
                      "red": 1, "green": 1, "blue": 1}}}},
        "fields": "fontSize,bold,foregroundColor"}},

    # Slide 2 — content
    {"createSlide": {"objectId": "s2", "insertionIndex": 1,
                     "slideLayoutReference": {"predefinedLayout": "BLANK"}}},
    {"createShape": {"objectId": "s2_h", "shapeType": "TEXT_BOX",
                     "elementProperties": {
                         "pageObjectId": "s2",
                         "size": {"width": {"magnitude": 600, "unit": "PT"},
                                  "height": {"magnitude": 60, "unit": "PT"}},
                         "transform": {"scaleX": 1, "scaleY": 1,
                                       "translateX": 60, "translateY": 50, "unit": "PT"}}}},
    {"insertText": {"objectId": "s2_h", "text": "Highlights", "insertionIndex": 0}},
    {"updateTextStyle": {"objectId": "s2_h", "textRange": {"type": "ALL"},
                         "style": {"fontSize": {"magnitude": 32, "unit": "PT"}, "bold": True},
                         "fields": "fontSize,bold"}},

    {"createShape": {"objectId": "s2_body", "shapeType": "TEXT_BOX",
                     "elementProperties": {
                         "pageObjectId": "s2",
                         "size": {"width": {"magnitude": 600, "unit": "PT"},
                                  "height": {"magnitude": 250, "unit": "PT"}},
                         "transform": {"scaleX": 1, "scaleY": 1,
                                       "translateX": 60, "translateY": 130, "unit": "PT"}}}},
    {"insertText": {"objectId": "s2_body",
                    "text": "Shipped feature A\nLaunched product B\nClosed deal C",
                    "insertionIndex": 0}},
    {"createParagraphBullets": {"objectId": "s2_body",
                                "textRange": {"type": "ALL"},
                                "bulletPreset": "BULLET_DISC_CIRCLE_SQUARE"}},
]

slides.presentations().batchUpdate(presentationId=pid, body={"requests": requests}).execute()
print(f"https://docs.google.com/presentation/d/{pid}/edit")
```

---

## Patterns worth knowing

**Avoid placeholders, build everything yourself.** Predefined layouts inject placeholder shapes that resist the styles you set. For consistent visual control, use `BLANK` layouts and build shapes manually.

**Group related slides into one batchUpdate.** The Slides API allows hundreds of requests per batch. Sending them as one batch is faster and atomic — if one fails, none apply. Splitting into many calls means you might end up with half a deck.

**Keep an `objectId` naming convention.** Something like `s1_title`, `s1_body`, `s2_card_1`, `s2_card_1_bg` so you can mentally map IDs to slides without re-reading the deck.

**Set `fields` precisely.** When updating styles, `fields` is a comma-separated mask of which properties to apply. If you don't list a field, it's not updated — but if you list a field your `style` dict doesn't contain, it gets reset to default. Set both consistently.

**For repeating layouts (e.g., 6 capability cards), build with a helper.** Write a Python function that takes `(slide_id, x, y, w, h, color, title, body)` and returns the list of requests. Call it 6 times. Don't paste 6 copies of the same JSON.
