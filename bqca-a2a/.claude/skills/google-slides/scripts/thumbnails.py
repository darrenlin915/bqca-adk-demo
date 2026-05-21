"""Export each slide of a Google Slides presentation as a PNG.

Uses presentations.pages().getThumbnail() — same image the Slides editor renders.
No LibreOffice / PDF conversion needed.

Usage:
    python thumbnails.py <presentationId-or-url>
    python thumbnails.py <pid> -o ./out/                  # output directory (default: cwd)
    python thumbnails.py <pid> --slides 1,3,5             # only specific slides (1-based)
    python thumbnails.py <pid> --size LARGE               # SMALL | MEDIUM | LARGE (default LARGE)
    python thumbnails.py <pid> --mime PNG                 # PNG (default) — Slides API only supports PNG today
    python thumbnails.py <pid> --prefix shot              # file prefix (default "slide")
    python thumbnails.py <pid> --json                     # machine-readable output

Output filenames: <prefix>-01.png, <prefix>-02.png, ... (zero-padded to match slide count width).

NOTE: getThumbnail returns a signed URL that expires in ~30 minutes. This script
downloads immediately, so don't worry about it unless you save the URLs yourself.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from auth import get_credentials  # noqa: E402

URL_RE = re.compile(r"docs\.google\.com/presentation/d/([a-zA-Z0-9_-]+)")
VALID_SIZES = ("SMALL", "MEDIUM", "LARGE")
VALID_MIMES = ("PNG",)  # Slides API only supports PNG at the time of writing


def parse_presentation_id(arg: str) -> str:
    m = URL_RE.search(arg)
    if m:
        return m.group(1)
    return arg.strip()


def parse_slide_selection(arg: str | None, total: int) -> list[int]:
    """Parse '1,3,5' or '1-3,7' into a sorted list of 1-based indices, clamped to [1, total]."""
    if not arg:
        return list(range(1, total + 1))
    out: set[int] = set()
    for part in arg.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            lo, hi = part.split("-", 1)
            for i in range(int(lo), int(hi) + 1):
                if 1 <= i <= total:
                    out.add(i)
        else:
            i = int(part)
            if 1 <= i <= total:
                out.add(i)
    return sorted(out)


def export(
    presentation_id: str,
    out_dir: Path,
    slides_arg: str | None = None,
    size: str = "LARGE",
    mime: str = "PNG",
    prefix: str = "slide",
) -> list[Path]:
    from googleapiclient.discovery import build

    service = build("slides", "v1", credentials=get_credentials())

    pres = service.presentations().get(
        presentationId=presentation_id,
        fields="title,slides.objectId",
    ).execute()

    slides = pres.get("slides", [])
    total = len(slides)
    if total == 0:
        print(f"[thumbnails] presentation '{pres.get('title', presentation_id)}' has no slides", file=sys.stderr)
        return []

    selection = parse_slide_selection(slides_arg, total)
    if not selection:
        print(f"[thumbnails] no slides matched --slides={slides_arg!r} (total={total})", file=sys.stderr)
        return []

    out_dir.mkdir(parents=True, exist_ok=True)
    pad = max(2, len(str(total)))
    written: list[Path] = []

    for n, idx in enumerate(selection, 1):
        slide = slides[idx - 1]
        oid = slide["objectId"]

        result = service.presentations().pages().getThumbnail(
            presentationId=presentation_id,
            pageObjectId=oid,
            thumbnailProperties_thumbnailSize=size,
            thumbnailProperties_mimeType=mime,
        ).execute()

        url = result["contentUrl"]
        ext = mime.lower()
        out_path = out_dir / f"{prefix}-{idx:0{pad}d}.{ext}"
        urllib.request.urlretrieve(url, out_path)
        written.append(out_path)
        print(f"[{n}/{len(selection)}] {out_path.name} ✓", file=sys.stderr)

    return written


def main():
    parser = argparse.ArgumentParser(description="Export each Google Slides slide as a PNG.")
    parser.add_argument("presentation", help="Presentation ID or full Google Slides URL")
    parser.add_argument("-o", "--output", default=".", help="Output directory (default: current dir)")
    parser.add_argument("--slides", help="Comma/range list of 1-based slide indices (e.g. 1,3,5 or 2-4,7)")
    parser.add_argument("--size", choices=VALID_SIZES, default="LARGE", help="Thumbnail size (default LARGE)")
    parser.add_argument("--mime", choices=VALID_MIMES, default="PNG", help="Output format (default PNG)")
    parser.add_argument("--prefix", default="slide", help="Filename prefix (default 'slide')")
    parser.add_argument("--json", action="store_true", help="Print JSON list of written paths to stdout")
    args = parser.parse_args()

    pid = parse_presentation_id(args.presentation)
    out_dir = Path(args.output).expanduser().resolve()

    written = export(
        presentation_id=pid,
        out_dir=out_dir,
        slides_arg=args.slides,
        size=args.size,
        mime=args.mime,
        prefix=args.prefix,
    )

    if args.json:
        print(json.dumps([str(p) for p in written], indent=2))
    else:
        for p in written:
            print(p)


if __name__ == "__main__":
    main()
