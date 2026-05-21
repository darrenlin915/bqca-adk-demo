# ruff: noqa
"""create_bqca_report tool: turn the last BQCA analysis into a Google Slides deck.

Reads `tool_context.state["last_bqca_payload"]` (set by ask_bqca) and builds a
presentation with a styled title slide, one content slide per significant step
(schema, SQL, data table, chart), and a closer ("結論") slide. OAuth shares the
same credentials as ask_bqca — see `app.tools._negotiate_creds`.

Visual design follows the "Midnight Executive" palette from the project's
google-slides skill (see .claude/skills/google-slides/references/design.md):
sandwich structure — navy title + conclusion, white content with a thin navy
left accent strip as a repeated motif. Google Sans headers, Roboto body,
Roboto Mono SQL. Tables get a navy header row and alternating ice-blue tint.
"""

from __future__ import annotations

import base64
import io
import json
import logging
import uuid
from typing import Any, Optional

import requests
import vl_convert as vlc
from google.adk.tools import ToolContext
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

from app.tools import _negotiate_creds

logger = logging.getLogger(__name__)

# Slides default page size (widescreen 16:9, in EMU = 1/914400 inch).
SLIDE_WIDTH_EMU = 9144000
SLIDE_HEIGHT_EMU = 5143500
MARGIN_EMU = 457200
HEADER_HEIGHT_EMU = 600000
MAX_TABLE_ROWS = 20

# === Midnight Executive palette (0.0–1.0 floats, as Slides API expects) ===
NAVY = (0.118, 0.153, 0.380)       # #1E2761 — primary
ICE_BLUE = (0.792, 0.863, 0.988)   # #CADCFC — secondary
WHITE = (1.0, 1.0, 1.0)
SLATE_900 = (0.067, 0.094, 0.153)  # #111827 — body text on light bg
ROW_TINT = (0.957, 0.969, 0.988)   # #F4F7FC — alt-row tint

HEADER_FONT = "Google Sans"
BODY_FONT = "Roboto"
MONO_FONT = "Roboto Mono"

# Width of the navy left accent strip on every content slide (the visual motif).
ACCENT_STRIP_W_EMU = 121920  # ~9.6pt
# Content area starts past the accent strip + standard left margin.
CONTENT_X_EMU = ACCENT_STRIP_W_EMU + MARGIN_EMU
CONTENT_W_EMU = SLIDE_WIDTH_EMU - CONTENT_X_EMU - MARGIN_EMU


def _oid(prefix: str) -> str:
    """Slides object IDs must match [a-zA-Z0-9_], 5-50 chars."""
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def _rgb(rgb: tuple[float, float, float]) -> dict:
    return {"red": rgb[0], "green": rgb[1], "blue": rgb[2]}


def _set_page_background(slide_id: str, rgb: tuple[float, float, float]) -> dict:
    return {
        "updatePageProperties": {
            "objectId": slide_id,
            "pageProperties": {
                "pageBackgroundFill": {"solidFill": {"color": {"rgbColor": _rgb(rgb)}}}
            },
            "fields": "pageBackgroundFill.solidFill",
        }
    }


def _rectangle(
    slide_id: str,
    x: int,
    y: int,
    w: int,
    h: int,
    fill_rgb: Optional[tuple[float, float, float]] = None,
) -> list[dict]:
    """Solid rectangle, no visible border. For accent strips and color blocks."""
    shape_id = _oid("rect")
    reqs: list[dict] = [
        {
            "createShape": {
                "objectId": shape_id,
                "shapeType": "RECTANGLE",
                "elementProperties": {
                    "pageObjectId": slide_id,
                    "size": {
                        "width": {"magnitude": w, "unit": "EMU"},
                        "height": {"magnitude": h, "unit": "EMU"},
                    },
                    "transform": {
                        "scaleX": 1,
                        "scaleY": 1,
                        "translateX": x,
                        "translateY": y,
                        "unit": "EMU",
                    },
                },
            }
        }
    ]
    props: dict[str, Any] = {"outline": {"outlineFill": {"solidFill": {"alpha": 0}}}}
    fields = ["outline"]
    if fill_rgb is not None:
        props["shapeBackgroundFill"] = {"solidFill": {"color": {"rgbColor": _rgb(fill_rgb)}}}
        fields.append("shapeBackgroundFill.solidFill")
    reqs.append(
        {
            "updateShapeProperties": {
                "objectId": shape_id,
                "shapeProperties": props,
                "fields": ",".join(fields),
            }
        }
    )
    return reqs


def _upload_chart_to_drive(
    drive_service, vega_spec: dict, name: str
) -> tuple[Optional[str], Optional[str]]:
    """Render Vega-Lite → PNG → upload to Drive (anyone-with-link) → (url, file_id).

    Returns (None, None) if rendering fails so the caller can skip the chart
    slide. The public ACL is required for Slides `createImage` to fetch the
    bytes; the caller is responsible for deleting the file after batchUpdate
    completes (Slides caches the bytes server-side at insert time).
    """
    try:
        png_bytes = vlc.vegalite_to_png(json.dumps(vega_spec), scale=2.0)
    except Exception as e:
        logger.warning("vega-lite render failed for %s: %s", name, e)
        return None, None

    media = MediaIoBaseUpload(io.BytesIO(png_bytes), mimetype="image/png")
    f = drive_service.files().create(
        body={"name": name, "mimeType": "image/png"},
        media_body=media,
        fields="id",
    ).execute()
    file_id = f["id"]
    drive_service.permissions().create(
        fileId=file_id,
        body={"type": "anyone", "role": "reader"},
    ).execute()
    # Use the Drive thumbnail endpoint: returns raw image bytes (no HTML
    # interstitial) and is what Slides API can actually fetch via createImage.
    # `drive.google.com/uc?export=download` returns an interstitial for any file
    # the scanner deems risky, which fails the createImage request.
    return f"https://drive.google.com/thumbnail?id={file_id}&sz=w2000", file_id


def _text_box(
    slide_id: str,
    text: str,
    x: int,
    y: int,
    w: int,
    h: int,
    *,
    font_family: Optional[str] = None,
    font_size_pt: Optional[float] = None,
    color_rgb: Optional[tuple[float, float, float]] = None,
    bold: bool = False,
) -> list[dict]:
    """createShape + insertText (+ optional updateTextStyle) for a text box."""
    shape_id = _oid("tx")
    reqs: list[dict] = [
        {
            "createShape": {
                "objectId": shape_id,
                "shapeType": "TEXT_BOX",
                "elementProperties": {
                    "pageObjectId": slide_id,
                    "size": {
                        "width": {"magnitude": w, "unit": "EMU"},
                        "height": {"magnitude": h, "unit": "EMU"},
                    },
                    "transform": {
                        "scaleX": 1,
                        "scaleY": 1,
                        "translateX": x,
                        "translateY": y,
                        "unit": "EMU",
                    },
                },
            }
        },
        {"insertText": {"objectId": shape_id, "text": text}},
    ]
    style: dict[str, Any] = {}
    fields: list[str] = []
    if font_family:
        style["fontFamily"] = font_family
        fields.append("fontFamily")
    if font_size_pt:
        style["fontSize"] = {"magnitude": font_size_pt, "unit": "PT"}
        fields.append("fontSize")
    if color_rgb is not None:
        style["foregroundColor"] = {"opaqueColor": {"rgbColor": _rgb(color_rgb)}}
        fields.append("foregroundColor")
    if bold:
        style["bold"] = True
        fields.append("bold")
    if style:
        reqs.append(
            {
                "updateTextStyle": {
                    "objectId": shape_id,
                    "style": style,
                    "fields": ",".join(fields),
                    "textRange": {"type": "ALL"},
                }
            }
        )
    return reqs


def _decorate_content_slide(slide_id: str) -> list[dict]:
    """Apply the motif: white background + thin navy left accent strip."""
    reqs: list[dict] = [_set_page_background(slide_id, WHITE)]
    reqs.extend(
        _rectangle(slide_id, 0, 0, ACCENT_STRIP_W_EMU, SLIDE_HEIGHT_EMU, fill_rgb=NAVY)
    )
    return reqs


def _table_requests(slide_id: str, headers: list[str], rows: list[list[Any]]) -> list[dict]:
    """createTable + styled cells: navy header row, alternating ice-blue body rows."""
    table_id = _oid("tbl")
    truncated = rows[:MAX_TABLE_ROWS]
    n_rows = len(truncated) + 1  # +1 for header
    n_cols = max(len(headers), 1)

    table_x = CONTENT_X_EMU
    table_w = CONTENT_W_EMU
    table_y = MARGIN_EMU + HEADER_HEIGHT_EMU + 200000
    table_h = SLIDE_HEIGHT_EMU - table_y - MARGIN_EMU

    reqs: list[dict] = [
        {
            "createTable": {
                "objectId": table_id,
                "elementProperties": {
                    "pageObjectId": slide_id,
                    "size": {
                        "width": {"magnitude": table_w, "unit": "EMU"},
                        "height": {"magnitude": table_h, "unit": "EMU"},
                    },
                    "transform": {
                        "scaleX": 1,
                        "scaleY": 1,
                        "translateX": table_x,
                        "translateY": table_y,
                        "unit": "EMU",
                    },
                },
                "rows": n_rows,
                "columns": n_cols,
            }
        }
    ]

    # Header row — navy background across all cells
    reqs.append(
        {
            "updateTableCellProperties": {
                "objectId": table_id,
                "tableRange": {
                    "location": {"rowIndex": 0, "columnIndex": 0},
                    "rowSpan": 1,
                    "columnSpan": n_cols,
                },
                "tableCellProperties": {
                    "tableCellBackgroundFill": {"solidFill": {"color": {"rgbColor": _rgb(NAVY)}}}
                },
                "fields": "tableCellBackgroundFill.solidFill",
            }
        }
    )
    for col, h in enumerate(headers):
        reqs.append(
            {
                "insertText": {
                    "objectId": table_id,
                    "cellLocation": {"rowIndex": 0, "columnIndex": col},
                    "text": str(h),
                }
            }
        )
        reqs.append(
            {
                "updateTextStyle": {
                    "objectId": table_id,
                    "cellLocation": {"rowIndex": 0, "columnIndex": col},
                    "style": {
                        "foregroundColor": {"opaqueColor": {"rgbColor": _rgb(WHITE)}},
                        "bold": True,
                        "fontFamily": HEADER_FONT,
                        "fontSize": {"magnitude": 11, "unit": "PT"},
                    },
                    "fields": "foregroundColor,bold,fontFamily,fontSize",
                    "textRange": {"type": "ALL"},
                }
            }
        )

    # Body rows
    for r, row in enumerate(truncated):
        # Alt-row tint on odd body rows
        if r % 2 == 1:
            reqs.append(
                {
                    "updateTableCellProperties": {
                        "objectId": table_id,
                        "tableRange": {
                            "location": {"rowIndex": r + 1, "columnIndex": 0},
                            "rowSpan": 1,
                            "columnSpan": n_cols,
                        },
                        "tableCellProperties": {
                            "tableCellBackgroundFill": {
                                "solidFill": {"color": {"rgbColor": _rgb(ROW_TINT)}}
                            }
                        },
                        "fields": "tableCellBackgroundFill.solidFill",
                    }
                }
            )
        for c in range(n_cols):
            val = row[c] if c < len(row) else ""
            text = "" if val is None else str(val)
            reqs.append(
                {
                    "insertText": {
                        "objectId": table_id,
                        "cellLocation": {"rowIndex": r + 1, "columnIndex": c},
                        "text": text,
                    }
                }
            )
            if text:
                reqs.append(
                    {
                        "updateTextStyle": {
                            "objectId": table_id,
                            "cellLocation": {"rowIndex": r + 1, "columnIndex": c},
                            "style": {
                                "fontFamily": BODY_FONT,
                                "fontSize": {"magnitude": 10, "unit": "PT"},
                                "foregroundColor": {"opaqueColor": {"rgbColor": _rgb(SLATE_900)}},
                            },
                            "fields": "fontFamily,fontSize,foregroundColor",
                            "textRange": {"type": "ALL"},
                        }
                    }
                )
    return reqs


def _build_title_slide(title: str, subtitle: str) -> list[dict]:
    """Title slide: navy background, large white title, ice-blue subtitle, side bar."""
    sid = _oid("title")
    reqs: list[dict] = [
        {
            "createSlide": {
                "objectId": sid,
                "insertionIndex": 0,
                "slideLayoutReference": {"predefinedLayout": "BLANK"},
            }
        },
        _set_page_background(sid, NAVY),
    ]
    # Wider ice-blue accent strip on title (3× the content motif) as the visual hook
    reqs.extend(
        _rectangle(sid, 0, 0, ACCENT_STRIP_W_EMU * 3, SLIDE_HEIGHT_EMU, fill_rgb=ICE_BLUE)
    )
    text_x = ACCENT_STRIP_W_EMU * 3 + MARGIN_EMU
    text_w = SLIDE_WIDTH_EMU - text_x - MARGIN_EMU

    # Auto-shrink title if it's very long (Chinese question text can run long)
    if len(title) <= 24:
        title_pt = 44
    elif len(title) <= 40:
        title_pt = 36
    else:
        title_pt = 28

    title_h = 1300000
    title_y = SLIDE_HEIGHT_EMU // 2 - title_h
    reqs.extend(
        _text_box(
            sid, title, text_x, title_y, text_w, title_h,
            font_family=HEADER_FONT, font_size_pt=title_pt,
            color_rgb=WHITE, bold=True,
        )
    )
    reqs.extend(
        _text_box(
            sid, subtitle,
            text_x, title_y + title_h + 100000, text_w, 700000,
            font_family=BODY_FONT, font_size_pt=18, color_rgb=ICE_BLUE,
        )
    )
    return reqs


def _build_conclusion_slide(answer: str) -> list[dict]:
    """Conclusion slide: mirrors title — navy background, large white text."""
    sid = _oid("end")
    reqs: list[dict] = [
        {
            "createSlide": {
                "objectId": sid,
                "slideLayoutReference": {"predefinedLayout": "BLANK"},
            }
        },
        _set_page_background(sid, NAVY),
    ]
    reqs.extend(
        _rectangle(sid, 0, 0, ACCENT_STRIP_W_EMU * 3, SLIDE_HEIGHT_EMU, fill_rgb=ICE_BLUE)
    )
    text_x = ACCENT_STRIP_W_EMU * 3 + MARGIN_EMU
    text_w = SLIDE_WIDTH_EMU - text_x - MARGIN_EMU
    # Small "結論" label in ice blue, then big white answer
    reqs.extend(
        _text_box(
            sid, "結論",
            text_x, MARGIN_EMU + 100000, text_w, 400000,
            font_family=HEADER_FONT, font_size_pt=14,
            color_rgb=ICE_BLUE, bold=True,
        )
    )
    reqs.extend(
        _text_box(
            sid, answer,
            text_x, MARGIN_EMU + 600000,
            text_w, SLIDE_HEIGHT_EMU - MARGIN_EMU - 700000,
            font_family=HEADER_FONT, font_size_pt=28,
            color_rgb=WHITE,
        )
    )
    return reqs


def _build_slide_requests(
    steps: list[dict], drive_service
) -> tuple[list[dict], list[str]]:
    """For each meaningful step, emit createSlide + content requests.

    The caller has already deleted the auto-created default slide and prepended
    the styled title slide; this only appends content + conclusion slides.

    Returns (requests, chart_file_ids). The caller must delete each chart file
    from Drive after batchUpdate completes — Slides caches the bytes at insert
    time so the public temp file is no longer needed.
    """
    reqs: list[dict] = []
    chart_file_ids: list[str] = []

    def _new_content_slide() -> str:
        sid = _oid("sl")
        reqs.append(
            {
                "createSlide": {
                    "objectId": sid,
                    "slideLayoutReference": {"predefinedLayout": "BLANK"},
                }
            }
        )
        reqs.extend(_decorate_content_slide(sid))
        return sid

    def _header(slide_id: str, text: str, font_size_pt: float = 26) -> None:
        reqs.extend(
            _text_box(
                slide_id, text,
                CONTENT_X_EMU, MARGIN_EMU,
                CONTENT_W_EMU, HEADER_HEIGHT_EMU,
                font_family=HEADER_FONT, font_size_pt=font_size_pt,
                color_rgb=NAVY, bold=True,
            )
        )

    def _body(
        slide_id: str,
        text: str,
        *,
        font_family: Optional[str] = None,
        font_size_pt: float = 14,
    ) -> None:
        body_y = MARGIN_EMU + HEADER_HEIGHT_EMU + 200000
        body_h = SLIDE_HEIGHT_EMU - body_y - MARGIN_EMU
        reqs.extend(
            _text_box(
                slide_id, text,
                CONTENT_X_EMU, body_y,
                CONTENT_W_EMU, body_h,
                font_family=font_family or BODY_FONT,
                font_size_pt=font_size_pt,
                color_rgb=SLATE_900,
            )
        )

    for step in steps:
        for kind, payload in step.items():
            if kind == "Schema Resolved":
                sid = _new_content_slide()
                _header(sid, "涉及資料表")
                _body(sid, "\n".join(f"•  {t}" for t in payload))

            elif kind == "SQL Generated":
                sid = _new_content_slide()
                _header(sid, "Generated SQL")
                _body(sid, str(payload), font_family=MONO_FONT, font_size_pt=10)

            elif kind == "Data Retrieved":
                sid = _new_content_slide()
                summary = payload.get("summary", "查詢結果")
                _header(sid, f"查詢結果 — {summary}", font_size_pt=20)
                reqs.extend(_table_requests(sid, payload["headers"], payload["rows"]))

            elif kind == "Chart Spec":
                url, fid = _upload_chart_to_drive(
                    drive_service, payload, f"bqca-chart-{uuid.uuid4().hex[:8]}.png"
                )
                if not url:
                    continue
                if fid:
                    chart_file_ids.append(fid)
                sid = _new_content_slide()
                _header(sid, "圖表分析", font_size_pt=22)
                img_id = _oid("img")
                img_y = MARGIN_EMU + HEADER_HEIGHT_EMU + 200000
                img_h = SLIDE_HEIGHT_EMU - img_y - MARGIN_EMU
                reqs.append(
                    {
                        "createImage": {
                            "objectId": img_id,
                            "url": url,
                            "elementProperties": {
                                "pageObjectId": sid,
                                "size": {
                                    "width": {"magnitude": CONTENT_W_EMU, "unit": "EMU"},
                                    "height": {"magnitude": img_h, "unit": "EMU"},
                                },
                                "transform": {
                                    "scaleX": 1,
                                    "scaleY": 1,
                                    "translateX": CONTENT_X_EMU,
                                    "translateY": img_y,
                                    "unit": "EMU",
                                },
                            },
                        }
                    }
                )

            elif kind == "Answer":
                reqs.extend(_build_conclusion_slide(str(payload)))

    return reqs, chart_file_ids


def create_bqca_report(title: Optional[str], tool_context: ToolContext) -> dict:
    """Build a Google Slides deck from the most recent ask_bqca result.

    Reads `state["last_bqca_payload"]` (set by ask_bqca) and creates a new
    presentation with a styled title slide, one content slide per significant
    step (schema, SQL, data table, chart), and a closer ("結論") slide. Returns
    a link plus per-page MEDIUM PNG thumbnails for inline preview in the chat.

    Args:
        title: Optional presentation title. Defaults to "ApexZenith Games 分析：<question>".

    Returns:
        dict with `status` ("SUCCESS", "ERROR", or "PENDING_AUTH"):
        - SUCCESS: {"presentation_id", "url", "thumbnails_b64": [b64 strings]}
        - PENDING_AUTH: {"message"}
        - ERROR: {"error_details"}
    """
    payload = tool_context.state.get("last_bqca_payload")
    if not payload:
        return {
            "status": "ERROR",
            "error_details": "尚無 BQCA 分析結果可供轉成簡報，請先詢問一個 VIP 分析問題。",
        }

    creds = _negotiate_creds(tool_context)
    if isinstance(creds, dict):
        return {"status": "PENDING_AUTH", **creds}

    try:
        slides_svc = build("slides", "v1", credentials=creds, cache_discovery=False)
        drive_svc = build("drive", "v3", credentials=creds, cache_discovery=False)

        question = payload["question"]
        pres_title = title or f"ApexZenith Games 分析：{question}"
        pres = slides_svc.presentations().create(body={"title": pres_title}).execute()
        pid = pres["presentationId"]

        # Delete the auto-created default slide — we build our own title slide
        # on BLANK layout so we have full styling control (no placeholders to
        # fight). Then append all content slides + conclusion.
        all_reqs: list[dict] = []
        if pres.get("slides"):
            all_reqs.append({"deleteObject": {"objectId": pres["slides"][0]["objectId"]}})
        all_reqs.extend(_build_title_slide(pres_title, question))
        slide_reqs, chart_file_ids = _build_slide_requests(payload["steps"], drive_svc)
        all_reqs.extend(slide_reqs)

        if all_reqs:
            slides_svc.presentations().batchUpdate(
                presentationId=pid, body={"requests": all_reqs}
            ).execute()

        # Slides has now cached the chart bytes server-side. Delete the temp
        # public-link files from the user's Drive so the VIP chart images
        # don't linger as anyone-with-link forever. Failures are non-fatal.
        for fid in chart_file_ids:
            try:
                drive_svc.files().delete(fileId=fid).execute()
            except Exception:
                logger.warning("Failed to delete temp chart file %s", fid)

        # Re-fetch to discover the final page list (createSlide assigned IDs),
        # then thumbnail each page so the chat can preview them inline.
        pres_full = slides_svc.presentations().get(presentationId=pid).execute()
        thumbnails: list[bytes] = []
        for page in pres_full.get("slides", []):
            thumb = (
                slides_svc.presentations()
                .pages()
                .getThumbnail(
                    presentationId=pid,
                    pageObjectId=page["objectId"],
                    thumbnailProperties_thumbnailSize="MEDIUM",
                )
                .execute()
            )
            thumbnails.append(requests.get(thumb["contentUrl"], timeout=30).content)

        # base64-encode bytes: ADK serializes function_response → JSON history,
        # and raw bytes break that path with "Object of type bytes is not JSON
        # serializable". The before_model_callback decodes them back into Blob.
        return {
            "status": "SUCCESS",
            "presentation_id": pid,
            "url": f"https://docs.google.com/presentation/d/{pid}/edit",
            "thumbnails_b64": [base64.b64encode(t).decode("ascii") for t in thumbnails],
        }
    except Exception:
        logger.exception("create_bqca_report failed")
        return {
            "status": "ERROR",
            "error_details": "簡報產生失敗，請稍後再試。詳細錯誤已記錄於伺服器日誌。",
        }
