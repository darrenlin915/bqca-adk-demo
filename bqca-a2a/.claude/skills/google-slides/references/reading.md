# Reading & Parsing Google Slides

For most "extract the text" tasks, just use the bundled script:

```bash
python scripts/extract_text.py <presentationId-or-url>
```

Read this file when you need more — finding a specific slide, parsing tables, pulling image references, or building a custom transformation of the content.

---

## The presentation object structure

`presentations.get(presentationId).execute()` returns a JSON object that looks like:

```
{
  "presentationId": "...",
  "title": "Q2 OKR Review",
  "pageSize": {"width": {...}, "height": {...}},
  "slides": [
    {
      "objectId": "p",
      "pageElements": [
        {"objectId": "title_box", "shape": {"shapeType": "TEXT_BOX", "text": {...}}},
        {"objectId": "img_1", "image": {...}},
        {"objectId": "table_1", "table": {...}},
        ...
      ],
      "slideProperties": {
        "notesPage": {
          "pageElements": [
            {"shape": {"shapeType": "TEXT_BOX",
                       "placeholder": {"type": "BODY"},
                       "text": {...}}}
          ],
          "notesProperties": {"speakerNotesObjectId": "..."}
        }
      }
    }
  ]
}
```

The interesting thing is each slide's `pageElements` array — that's the rendered content. Speaker notes live separately under `slideProperties.notesPage`.

---

## Reading text from a shape

A shape's `text` field contains a list of `textElements`, each of which is one of:

- `paragraphMarker` — marks the end of a paragraph; carries paragraph-level info (bullet, indent)
- `textRun` — actual text + style
- `autoText` — dynamic content like slide number

The text content is the concatenation of all `textRun.content` strings, in order. Paragraphs are delimited by `paragraphMarker`.

```python
def text_of(shape):
    out = []
    for el in shape.get("text", {}).get("textElements", []):
        if "textRun" in el:
            out.append(el["textRun"].get("content", ""))
    return "".join(out)
```

For bullet lists, the `paragraphMarker` will have a `bullet` field. The bundled `extract_text.py` handles this — read its `_collect_text_from_text_elements` function for a working version.

---

## Finding a specific slide

By position:

```python
slide = pres["slides"][2]  # third slide
```

By title text:

```python
def find_slide_by_title(pres, title_substring):
    for slide in pres["slides"]:
        for pe in slide.get("pageElements", []):
            if "shape" in pe:
                text = text_of(pe["shape"])
                if title_substring in text:
                    return slide
    return None
```

By layout (e.g., all "TITLE" slides):

```python
title_slides = [
    s for s in pres["slides"]
    if s.get("slideProperties", {}).get("layoutObjectId", "").endswith("TITLE")
]
```

---

## Reading tables

```python
def parse_table(pe):
    table = pe["table"]
    rows = []
    for row in table.get("tableRows", []):
        row_out = []
        for cell in row.get("tableCells", []):
            row_out.append(text_of(cell) if cell.get("text") else "")
        rows.append(row_out)
    return rows
```

Table cells contain shapes too — `cell["text"]` is the same `text` structure as a regular shape.

---

## Reading images

```python
for pe in slide.get("pageElements", []):
    if "image" in pe:
        url = pe["image"].get("contentUrl")  # signed URL, expires
        source = pe["image"].get("sourceUrl")  # original URL if available
        alt = pe.get("description") or pe["image"].get("title")
```

`contentUrl` is a temporary signed URL pointing to the rendered image. To download:

```python
import urllib.request
urllib.request.urlretrieve(url, "out.png")
```

---

## Reading speaker notes

```python
def speaker_notes(slide):
    notes_page = slide.get("slideProperties", {}).get("notesPage")
    if not notes_page:
        return ""
    for pe in notes_page.get("pageElements", []):
        shape = pe.get("shape", {})
        # The notes shape has placeholder.type == "BODY"
        if shape.get("placeholder", {}).get("type") == "BODY":
            return text_of(shape)
    return ""
```

---

## Thumbnails

To get a rendered image of a single slide:

```python
result = slides.presentations().pages().getThumbnail(
    presentationId=pid,
    pageObjectId=slide_id,
    thumbnailProperties_thumbnailSize="LARGE",     # SMALL/MEDIUM/LARGE
    thumbnailProperties_mimeType="PNG",
).execute()
url = result["contentUrl"]  # signed URL, ~30 min lifetime
```

For visual QA of generated decks: get a thumbnail of each slide, save them, and inspect (or hand to a vision-capable subagent for inspection).

---

## Common transformations

**Plain text dump for an LLM-friendly summary** → use `extract_text.py`.

**Bullet outline for a doc** → walk slides, treat the largest text on each as the title, smaller bullets below.

**Translation source** → enumerate every `textRun.content` with its `(slideIndex, objectId, range)` so you can write back via `updateTextStyle` / `replaceAllText` later.

**Search for placeholder leftovers** in a generated deck (`{{`, `lorem`, `xxxx`):

```bash
python scripts/extract_text.py <pid> | grep -iE "\{\{|lorem|xxxx"
```
