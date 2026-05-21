"""Extract text content from a Google Slides presentation as markdown.

Usage:
    python extract_text.py <presentationId>
    python extract_text.py <https://docs.google.com/presentation/d/.../edit>
    python extract_text.py <id-or-url> --notes      # include speaker notes (default on)
    python extract_text.py <id-or-url> --no-notes   # skip speaker notes
    python extract_text.py <id-or-url> -o output.md # write to file instead of stdout

The output is a markdown document with one section per slide:
    # Slide 1
    Title
    - bullet
    - bullet

    Notes:
    > speaker notes here
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# Make sibling import work whether run from skill dir or elsewhere
sys.path.insert(0, str(Path(__file__).parent))
from auth import get_credentials  # noqa: E402


URL_RE = re.compile(r"docs\.google\.com/presentation/d/([a-zA-Z0-9_-]+)")


def parse_presentation_id(arg: str) -> str:
    """Accept either a raw presentationId or a full Slides URL."""
    m = URL_RE.search(arg)
    if m:
        return m.group(1)
    return arg.strip()


def _collect_text_from_text_elements(text_elements):
    """Walk a textElements list and return concatenated text + a hint about lists."""
    lines = []
    current = []
    bullet_marker = False

    for el in text_elements or []:
        if "paragraphMarker" in el:
            # End of a paragraph: flush
            if current:
                line = "".join(current).rstrip("\n")
                if bullet_marker and line:
                    line = "- " + line
                if line:
                    lines.append(line)
                current = []
            bullet_marker = "bullet" in el["paragraphMarker"]
        elif "textRun" in el:
            current.append(el["textRun"].get("content", ""))
        elif "autoText" in el:
            # slide number etc. — skip for plain text extraction
            pass

    # Flush trailing
    if current:
        line = "".join(current).rstrip("\n")
        if bullet_marker and line:
            line = "- " + line
        if line:
            lines.append(line)

    return lines


def _extract_from_page_elements(page_elements):
    """Pull text out of every shape / table cell on a page, in order."""
    blocks = []
    for pe in page_elements or []:
        if "shape" in pe:
            text = pe["shape"].get("text")
            if text:
                lines = _collect_text_from_text_elements(text.get("textElements"))
                if lines:
                    blocks.append("\n".join(lines))
        elif "table" in pe:
            for row in pe["table"].get("tableRows", []):
                row_cells = []
                for cell in row.get("tableCells", []):
                    text = cell.get("text")
                    if text:
                        cell_lines = _collect_text_from_text_elements(text.get("textElements"))
                        row_cells.append(" ".join(cell_lines))
                    else:
                        row_cells.append("")
                blocks.append(" | ".join(row_cells))
        elif "elementGroup" in pe:
            blocks.extend(_extract_from_page_elements(pe["elementGroup"].get("children", [])))
        elif "image" in pe:
            alt = pe["image"].get("title") or pe.get("description") or "image"
            blocks.append(f"![{alt}]")
    return blocks


def extract(presentation_id: str, include_notes: bool = True) -> str:
    """Build a markdown string for the presentation."""
    from googleapiclient.discovery import build

    service = build("slides", "v1", credentials=get_credentials())
    pres = service.presentations().get(presentationId=presentation_id).execute()

    out = [f"# {pres.get('title', '(untitled)')}\n"]
    out.append(f"_presentationId: `{presentation_id}`_\n")

    for idx, slide in enumerate(pres.get("slides", []), 1):
        out.append(f"\n## Slide {idx}\n")
        blocks = _extract_from_page_elements(slide.get("pageElements"))
        if blocks:
            out.append("\n\n".join(blocks))
        else:
            out.append("_(no text)_")

        if include_notes:
            notes_page = slide.get("slideProperties", {}).get("notesPage")
            if notes_page:
                notes_blocks = _extract_from_page_elements(notes_page.get("pageElements"))
                # The notes page has a placeholder shape that contains the actual notes
                non_empty = [b for b in notes_blocks if b.strip()]
                if non_empty:
                    out.append("\n\n**Notes:**\n")
                    for b in non_empty:
                        for line in b.splitlines():
                            out.append(f"> {line}")

    return "\n".join(out) + "\n"


def main():
    parser = argparse.ArgumentParser(description="Extract text from a Google Slides presentation as markdown.")
    parser.add_argument("presentation", help="Presentation ID or full Google Slides URL")
    parser.add_argument("--no-notes", dest="notes", action="store_false", default=True,
                        help="Skip speaker notes (default: include)")
    parser.add_argument("-o", "--output", help="Write to a file instead of stdout")
    args = parser.parse_args()

    pid = parse_presentation_id(args.presentation)
    md = extract(pid, include_notes=args.notes)

    if args.output:
        Path(args.output).write_text(md)
        print(f"✓ wrote {args.output}", file=sys.stderr)
    else:
        sys.stdout.write(md)


if __name__ == "__main__":
    main()
