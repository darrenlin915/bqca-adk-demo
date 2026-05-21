"""Upload a local .pptx file to Google Drive and convert it to Google Slides.

The Drive API auto-converts when you set the upload mimeType to .pptx and the
target mimeType to Google Slides.

Usage:
    python upload_pptx.py deck.pptx
    python upload_pptx.py deck.pptx --title "Q2 Review"
    python upload_pptx.py deck.pptx --folder-id 1abc...
    python upload_pptx.py deck.pptx --json   # machine-readable output

Outputs the new presentation's id, title, and webViewLink.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from auth import get_credentials  # noqa: E402

PPTX_MIME = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
SLIDES_MIME = "application/vnd.google-apps.presentation"


def upload(pptx_path: Path, title: str | None = None, folder_id: str | None = None) -> dict:
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload

    if not pptx_path.exists():
        raise FileNotFoundError(f"No such file: {pptx_path}")
    if pptx_path.suffix.lower() != ".pptx":
        print(f"[upload_pptx] warning: file extension is not .pptx ({pptx_path.suffix})", file=sys.stderr)

    drive = build("drive", "v3", credentials=get_credentials())

    metadata = {
        "name": title or pptx_path.stem,
        "mimeType": SLIDES_MIME,  # convert during upload
    }
    if folder_id:
        metadata["parents"] = [folder_id]

    media = MediaFileUpload(str(pptx_path), mimetype=PPTX_MIME, resumable=True)

    file = drive.files().create(
        body=metadata,
        media_body=media,
        fields="id, name, webViewLink, mimeType",
        supportsAllDrives=True,
    ).execute()

    return file


def main():
    parser = argparse.ArgumentParser(description="Upload a .pptx and convert to Google Slides")
    parser.add_argument("pptx", help="Path to the .pptx file")
    parser.add_argument("--title", help="Title for the Google Slides presentation (default: filename without extension)")
    parser.add_argument("--folder-id", help="Drive folder ID to place the file in (default: My Drive root)")
    parser.add_argument("--json", action="store_true", help="Print JSON output instead of human-readable")
    args = parser.parse_args()

    file = upload(Path(args.pptx), title=args.title, folder_id=args.folder_id)

    if args.json:
        print(json.dumps(file, indent=2))
    else:
        print(f"✓ uploaded & converted to Google Slides")
        print(f"  presentationId: {file['id']}")
        print(f"  title:          {file['name']}")
        print(f"  url:            {file.get('webViewLink', 'https://docs.google.com/presentation/d/' + file['id'] + '/edit')}")


if __name__ == "__main__":
    main()
