# Editing an Existing Google Slides Presentation

All edits go through `presentations.batchUpdate(presentationId, body={"requests": [...]})`. Same model as creation — it's just that you reference existing `objectId`s instead of always making new ones.

---

## Find what you want to edit first

For non-trivial edits, get the presentation, find the slides/shapes you care about, and capture their `objectId`s before sending updates.

```python
from googleapiclient.discovery import build
from scripts.auth import get_credentials

slides = build("slides", "v1", credentials=get_credentials())
pres = slides.presentations().get(presentationId=pid).execute()

# Example: find the title shape on slide 2
slide2 = pres["slides"][1]
title_shape = next(
    pe for pe in slide2["pageElements"]
    if pe.get("shape", {}).get("placeholder", {}).get("type") == "TITLE"
)
title_id = title_shape["objectId"]
```

For text-only changes, you can usually skip the lookup and use `replaceAllText` instead — see below.

---

## Text changes

### `replaceAllText` — best for templated decks

Write your deck once with placeholder strings (`{{title}}`, `{{date}}`, `{{name}}`), then swap them in.

```python
{"replaceAllText": {
    "containsText": {"text": "{{date}}", "matchCase": True},
    "replaceText": "2026-04-29"
}}
```

This applies across the whole deck. Restrict to specific slides via `pageObjectIds`:

```python
{"replaceAllText": {
    "containsText": {"text": "{{stat}}"},
    "replaceText": "+12%",
    "pageObjectIds": ["slide_summary"]
}}
```

### `deleteText` + `insertText` — for surgical edits to one shape

```python
{"deleteText": {
    "objectId": title_id,
    "textRange": {"type": "ALL"}
}},
{"insertText": {
    "objectId": title_id,
    "text": "Updated Title",
    "insertionIndex": 0
}}
```

Both requests in a single batchUpdate.

### `updateTextStyle` — change font/color/etc on existing text

```python
{"updateTextStyle": {
    "objectId": title_id,
    "textRange": {"type": "ALL"},  # or {"type": "FIXED_RANGE", "startIndex": 0, "endIndex": 5}
    "style": {
        "foregroundColor": {"opaqueColor": {"rgbColor": {
            "red": 0.93, "green": 0.27, "blue": 0.21}}}
    },
    "fields": "foregroundColor"
}}
```

---

## Slide-level changes

### Add a slide at a specific position

```python
{"createSlide": {
    "objectId": "new_slide",
    "insertionIndex": 3,  # zero-based, where it should appear
    "slideLayoutReference": {"predefinedLayout": "BLANK"}
}}
```

### Delete a slide

```python
{"deleteObject": {"objectId": slide_id}}
```

### Reorder slides

```python
{"updateSlidesPosition": {
    "slideObjectIds": ["slide_a", "slide_b"],
    "insertionIndex": 0   # move them to the front, in this order
}}
```

### Duplicate a slide (poor man's template)

```python
{"duplicateObject": {
    "objectId": "template_slide",
    "objectIds": {  # map old ID → new ID for the duplicate's children
        "template_slide":   "copy_slide",
        "template_title":   "copy_title",
        "template_body":    "copy_body"
    }
}}
```

If you don't supply `objectIds`, the API generates fresh IDs and returns them in the response — but you lose the ability to reference the new shapes in subsequent requests in the same batch.

---

## Shape position / size / fill

### Move a shape

```python
{"updatePageElementTransform": {
    "objectId": shape_id,
    "transform": {
        "scaleX": 1, "scaleY": 1,
        "translateX": 100, "translateY": 200,
        "unit": "PT"
    },
    "applyMode": "ABSOLUTE"   # or RELATIVE
}}
```

### Resize a shape

```python
{"updatePageElementsZOrder": {...}},  # send-to-back / bring-to-front

# For width/height directly, you'd typically delete + recreate. Or use:
{"updateShapeProperties": {
    "objectId": shape_id,
    "shapeProperties": {...},
    "fields": "..."
}}
```

There's no direct "resize" request; the size is tied to the original `createShape`. To resize, recreate or use `updatePageElementTransform` with scale factors.

### Change fill color

```python
{"updateShapeProperties": {
    "objectId": shape_id,
    "shapeProperties": {
        "shapeBackgroundFill": {"solidFill": {"color": {"rgbColor": {
            "red": 0.13, "green": 0.13, "blue": 0.14}}, "alpha": 1.0}}
    },
    "fields": "shapeBackgroundFill.solidFill"
}}
```

---

## Adding images and tables to existing slides

Same as creating from scratch — `createImage` and `createTable` requests, with `elementProperties.pageObjectId` pointing to the existing slide.

---

## Common scenarios

### "Change every instance of 'Q1' to 'Q2'"

Single request:
```python
{"replaceAllText": {"containsText": {"text": "Q1"}, "replaceText": "Q2"}}
```

### "Update the third slide's title and add a footnote"

```python
slide3 = pres["slides"][2]
title = next(pe for pe in slide3["pageElements"]
             if pe.get("shape", {}).get("placeholder", {}).get("type") == "TITLE")
title_id = title["objectId"]

requests = [
    {"deleteText": {"objectId": title_id, "textRange": {"type": "ALL"}}},
    {"insertText": {"objectId": title_id, "text": "New Title", "insertionIndex": 0}},
    # Add footnote shape
    {"createShape": {
        "objectId": "s3_footnote",
        "shapeType": "TEXT_BOX",
        "elementProperties": {
            "pageObjectId": slide3["objectId"],
            "size": {"width": {"magnitude": 600, "unit": "PT"},
                     "height": {"magnitude": 30, "unit": "PT"}},
            "transform": {"scaleX": 1, "scaleY": 1,
                          "translateX": 60, "translateY": 380, "unit": "PT"}
        }
    }},
    {"insertText": {"objectId": "s3_footnote",
                    "text": "Source: internal", "insertionIndex": 0}},
    {"updateTextStyle": {"objectId": "s3_footnote", "textRange": {"type": "ALL"},
                         "style": {"fontSize": {"magnitude": 10, "unit": "PT"},
                                   "italic": True},
                         "fields": "fontSize,italic"}}
]
```

### "Wipe a slide and rebuild it"

Delete all `pageElements` from the slide, then add fresh ones:

```python
slide = pres["slides"][2]
delete_requests = [{"deleteObject": {"objectId": pe["objectId"]}}
                   for pe in slide.get("pageElements", [])
                   if not pe.get("shape", {}).get("placeholder")]  # skip layout placeholders

create_requests = [...]  # whatever you want
slides.presentations().batchUpdate(
    presentationId=pid, body={"requests": delete_requests + create_requests}
).execute()
```

---

## Editing PPTX-converted decks

Decks that came from `upload_pptx.py` retain their layouts but the shape `objectId`s are auto-generated. You can't predict them — fetch the presentation first, then identify shapes by their text content or position.

`replaceAllText` works as long as the text is unique enough to match.
