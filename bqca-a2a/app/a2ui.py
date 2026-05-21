# ruff: noqa
"""A2UI v0.8 envelope construction for BQCA + Slides tool results.

Gemini Enterprise only supports A2UI v0.8 today, so this module emits the v0.8
shapes (`beginRendering`, `surfaceUpdate`, `dataModelUpdate`) and the discriminated
component dict (`{"id": "...", "component": {"Text": {...}}}`) per the standard
catalog at https://a2ui.org/specification/v0_8/standard_catalog_definition.json

Wire format: each envelope becomes an A2A `DataPart(data=envelope, metadata={
"mimeType": "application/json+a2ui"})`. ADK's part_converter only emits a real
A2A `DataPart` when the genai inline_data is `text/plain` and wrapped in
`<a2a_datapart_json>...</a2a_datapart_json>` — anything else becomes a `FilePart`,
which GE renders as "Unsupported attachment". So `envelopes_to_parts` serializes
the full DataPart JSON (with `kind: "data"`) inside that wrapper.
"""

from __future__ import annotations

import base64
import json
import logging
from itertools import count
from typing import Any

import vl_convert as vlc
from google.genai import types

logger = logging.getLogger(__name__)

A2UI_VERSION = "0.8"
A2UI_MIME = "application/json+a2ui"
STANDARD_CATALOG_ID = (
    "https://a2ui.org/specification/v0_8/standard_catalog_definition.json"
)
A2UI_EXTENSION_URI = "https://a2ui.org/a2a-extension/a2ui/v0.8"

# ADK's part_converter sentinel — see google.adk.a2a.converters.part_converter.
# When inline_data is text/plain wrapped in these tags, ADK parses the inner JSON
# as an a2a_types.DataPart and emits a real DataPart instead of a FilePart.
_ADK_DATAPART_OPEN = b"<a2a_datapart_json>"
_ADK_DATAPART_CLOSE = b"</a2a_datapart_json>"
_ADK_DATAPART_TEXT_MIME = "text/plain"

# Cap rendered table rows in the UI; the raw data still passes through the
# tool response unchanged. 50 rows of N columns would blow up component count.
MAX_TABLE_ROWS = 10


class _IdGen:
    """Per-surface unique component ID generator."""

    def __init__(self) -> None:
        self._c = count(1)

    def __call__(self, prefix: str) -> str:
        return f"{prefix}_{next(self._c)}"


def _png_data_url(png_bytes: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(png_bytes).decode()


# vl-convert's bundled fonts have no CJK glyphs. The Dockerfile installs
# fonts-noto-cjk; this name is what `fc-list` resolves to for Traditional
# Chinese on Debian-slim. Apply via spec.config so it covers every text mark
# (axis, legend, title) without the BQCA-generated spec having to know.
_CJK_FONT = "Noto Sans CJK TC"


def _inject_cjk_font(spec: dict) -> dict:
    cfg = spec.setdefault("config", {})
    cfg.setdefault("font", _CJK_FONT)
    return spec


def _render_vega_png(spec: dict) -> bytes | None:
    """Vega-Lite spec → PNG bytes. Returns None on failure."""
    try:
        return vlc.vegalite_to_png(json.dumps(_inject_cjk_font(spec)), scale=2.0)
    except Exception as e:
        logger.warning("vega-lite render failed: %s", e)
        return None


def _literal(s: str) -> dict:
    return {"literalString": s}


def _text(cid: str, text: str, usage_hint: str | None = None) -> dict:
    body: dict[str, Any] = {"text": _literal(text)}
    if usage_hint:
        body["usageHint"] = usage_hint
    return {"id": cid, "component": {"Text": body}}


def _column(
    cid: str,
    children: list[str],
    alignment: str = "stretch",
    distribution: str | None = None,
    weight: int | None = None,
) -> dict:
    body: dict[str, Any] = {
        "children": {"explicitList": children},
        "alignment": alignment,
    }
    if distribution:
        body["distribution"] = distribution
    out: dict[str, Any] = {"id": cid, "component": {"Column": body}}
    if weight is not None:
        out["weight"] = weight
    return out


def _row(
    cid: str,
    children: list[str],
    alignment: str = "center",
    distribution: str = "spaceBetween",
    weight: int | None = None,
) -> dict:
    body: dict[str, Any] = {
        "children": {"explicitList": children},
        "alignment": alignment,
        "distribution": distribution,
    }
    out: dict[str, Any] = {"id": cid, "component": {"Row": body}}
    if weight is not None:
        out["weight"] = weight
    return out


def _card(cid: str, child: str) -> dict:
    return {"id": cid, "component": {"Card": {"child": child}}}


def _image(
    cid: str,
    url: str,
    fit: str = "contain",
    usage_hint: str | None = None,
) -> dict:
    body: dict[str, Any] = {"url": _literal(url), "fit": fit}
    if usage_hint:
        body["usageHint"] = usage_hint
    return {"id": cid, "component": {"Image": body}}


def _button(
    cid: str,
    label_cid: str,
    action_name: str,
    context: dict | None = None,
    primary: bool = False,
) -> dict:
    action: dict[str, Any] = {"name": action_name}
    if context:
        # A2UI v0.8 spec: action.context is an array of {key, value} pairs
        # where value is typed (literalString / literalNumber / literalBoolean
        # / path). Passing a plain dict makes hosts (including GE) silently
        # drop the action because the payload fails schema validation.
        action["context"] = [
            {"key": k, "value": _literal(str(v))} for k, v in context.items()
        ]
    body = {"child": label_cid, "primary": primary, "action": action}
    return {"id": cid, "component": {"Button": body}}


def _divider(cid: str, axis: str = "horizontal") -> dict:
    return {"id": cid, "component": {"Divider": {"axis": axis}}}


def _surface_update_envelope(surface_id: str, components: list[dict]) -> dict:
    return {
        "surfaceUpdate": {"surfaceId": surface_id, "components": components}
    }


def _begin_rendering_envelope(surface_id: str, root: str) -> dict:
    return {
        "beginRendering": {
            "surfaceId": surface_id,
            "root": root,
            "catalogId": STANDARD_CATALOG_ID,
        }
    }


def _table_components(
    cid: _IdGen, headers: list[str], rows: list[list[Any]], summary: str
) -> tuple[str, list[dict]]:
    """Card → Column → header Row + data Rows + summary Text. Returns (card_id, components)."""
    components: list[dict] = []
    row_ids: list[str] = []

    def _add_text_row(values: list[Any], usage_hint: str | None = None) -> str:
        cell_ids: list[str] = []
        for v in values:
            tid = cid("cell")
            components.append(
                _text(tid, "" if v is None else str(v), usage_hint=usage_hint)
            )
            cell_ids.append(tid)
        rid = cid("row")
        components.append(_row(rid, cell_ids, distribution="spaceBetween"))
        return rid

    row_ids.append(_add_text_row(headers, usage_hint="caption"))

    visible = rows[:MAX_TABLE_ROWS]
    for r in visible:
        row_ids.append(_add_text_row(r))

    if len(rows) > MAX_TABLE_ROWS:
        truncated_id = cid("trunc")
        components.append(
            _text(
                truncated_id,
                f"(顯示前 {MAX_TABLE_ROWS} 列,共 {len(rows)} 列)",
                usage_hint="caption",
            )
        )
        row_ids.append(truncated_id)

    summary_id = cid("sum")
    components.append(_text(summary_id, summary, usage_hint="caption"))
    row_ids.append(summary_id)

    col_id = cid("table_col")
    components.append(_column(col_id, row_ids))
    card_id = cid("table_card")
    components.append(_card(card_id, col_id))
    return card_id, components


def _suggested_queries_components(
    cid: _IdGen, queries: list[str]
) -> tuple[str, list[dict]]:
    """Render follow-ups as Buttons that emit `send_user_message` userActions.

    Whether GE forwards these as a new user turn depends on the executor; if not,
    they at minimum render as readable chips listing the suggested questions.
    """
    components: list[dict] = []
    button_ids: list[str] = []
    for q in queries:
        label_id = cid("btn_label")
        components.append(_text(label_id, q))
        btn_id = cid("btn")
        components.append(
            _button(btn_id, label_id, "send_user_message", {"text": q})
        )
        button_ids.append(btn_id)
    col_id = cid("followups_col")
    components.append(_column(col_id, button_ids, alignment="start"))
    return col_id, components


def _clarification_components(
    cid: _IdGen, clarifications: list[dict]
) -> tuple[str, list[dict]]:
    """Render clarifications as MultipleChoice + submit button.

    Selection state binds to `/clarification/<idx>` so the executor can read
    it back from the inbound `userAction` context.
    """
    components: list[dict] = []
    container_children: list[str] = []

    for idx, c in enumerate(clarifications):
        label_id = cid("clar_label")
        components.append(_text(label_id, c["question"], usage_hint="h5"))
        container_children.append(label_id)

        picker_id = cid("picker")
        max_sel = len(c["options"]) if c["mode"] == "MULTI_SELECT" else 1
        components.append(
            {
                "id": picker_id,
                "component": {
                    "MultipleChoice": {
                        "options": [
                            {"label": _literal(o), "value": o}
                            for o in c["options"]
                        ],
                        "selections": {"path": f"/clarification/{idx}"},
                        "maxAllowedSelections": max_sel,
                    }
                },
            }
        )
        container_children.append(picker_id)

    submit_label_id = cid("clar_submit_label")
    components.append(_text(submit_label_id, "送出"))
    submit_id = cid("clar_submit")
    components.append(
        _button(
            submit_id,
            submit_label_id,
            "submit_clarification",
            primary=True,
        )
    )
    container_children.append(submit_id)

    col_id = cid("clar_col")
    components.append(_column(col_id, container_children, alignment="start"))
    return col_id, components


def bqca_steps_to_a2ui(
    steps: list[dict[str, Any]], surface_id: str = "data_agent"
) -> list[dict]:
    """Convert _format_messages step list into A2UI v0.8 envelopes.

    Returns [beginRendering, surfaceUpdate] for non-empty step lists.
    Empty input yields an empty list (caller decides fallback behavior).
    """
    if not steps:
        return []

    cid = _IdGen()
    components: list[dict] = []
    root_children: list[str] = []

    for step in steps:
        for kind, payload in step.items():
            if kind in (
                "Question",
                "Schema Resolved",
                "SQL Generated",
                "Chart Requested",
                "Answer",
            ):
                # Static text/code/headings render better as native markdown
                # text parts in GE (code block highlight, monospace). See
                # `bqca_markdown_parts` + agent.py's `emit_a2ui_for_tools`.
                continue

            elif kind == "Data Retrieved":
                # Table itself goes via markdown (capped at 25 rows); the CSV
                # export button stays in A2UI because it's interactive. The
                # send_user_message text MUST stay in sync with the trigger
                # phrase in app/agent.py's instruction — the LLM keys off
                # this literal.
                csv_label_id = cid("csv_btn_label")
                components.append(_text(csv_label_id, "匯出此結果為 CSV"))
                csv_btn_id = cid("csv_btn")
                components.append(
                    _button(
                        csv_btn_id,
                        csv_label_id,
                        "send_user_message",
                        {"text": "請匯出剛剛的查詢結果為 CSV"},
                    )
                )
                root_children.append(csv_btn_id)

            elif kind == "Chart Spec":
                png = _render_vega_png(payload)
                if png is None:
                    err_id = cid("chart_err")
                    components.append(
                        _text(err_id, "(圖表渲染失敗)", usage_hint="caption")
                    )
                    root_children.append(err_id)
                    continue
                img_id = cid("chart")
                components.append(_image(img_id, _png_data_url(png)))
                root_children.append(img_id)

            elif kind == "Error":
                err_id = cid("err_text")
                components.append(_text(err_id, f"⚠️ {payload}"))
                err_card_id = cid("err_card")
                components.append(_card(err_card_id, err_id))
                root_children.append(err_card_id)

            elif kind == "Suggested Queries":
                root_id, sq_comps = _suggested_queries_components(cid, payload)
                components.extend(sq_comps)
                root_children.append(root_id)

            elif kind == "Clarification":
                root_id, cl_comps = _clarification_components(cid, payload)
                components.extend(cl_comps)
                root_children.append(root_id)

            elif kind == "Thinking":
                # Thoughts are routed through native ADK Part(thought=True) so
                # GE renders them in its built-in "思考過程" disclosure rather
                # than inside the A2UI card. See `bqca_thoughts` + agent.py's
                # `emit_a2ui_for_tools` callback.
                continue

    if not root_children:
        return []

    components.append(_column("root", root_children, alignment="stretch"))

    return [
        _begin_rendering_envelope(surface_id, "root"),
        _surface_update_envelope(surface_id, components),
    ]


def slides_to_a2ui(
    url: str, thumbnails_b64: list[str], surface_id: str = "bqca_slides"
) -> list[dict]:
    """Card with thumbnail images + URL text. No openUrl primitive exists in
    the v0.8 standard catalog, so the URL is rendered as plain text for the
    user to click/copy."""
    cid = _IdGen()
    components: list[dict] = []
    children: list[str] = []

    headline_id = cid("slides_headline")
    components.append(_text(headline_id, "✅ 已產生簡報", usage_hint="h3"))
    children.append(headline_id)

    for i, b64 in enumerate(thumbnails_b64):
        img_id = cid("thumb")
        components.append(
            _image(
                img_id,
                f"data:image/png;base64,{b64}",
                fit="contain",
            )
        )
        children.append(img_id)

    url_text_id = cid("url_text")
    components.append(_text(url_text_id, url, usage_hint="caption"))
    children.append(url_text_id)

    col_id = cid("slides_col")
    components.append(_column(col_id, children, alignment="stretch"))
    components.append(_card("root", col_id))

    return [
        _begin_rendering_envelope(surface_id, "root"),
        _surface_update_envelope(surface_id, components),
    ]


def csv_to_a2ui(
    url: str,
    filename: str,
    row_count: int,
    surface_id: str = "bqca_csv",
) -> list[dict]:
    """Card with CSV metadata + URL text. v0.8 standard catalog has no openUrl
    primitive, so the URL is rendered as plain text for the user to click/copy
    (mirrors slides_to_a2ui)."""
    cid = _IdGen()
    components: list[dict] = []
    children: list[str] = []

    headline_id = cid("csv_headline")
    components.append(_text(headline_id, "✅ CSV 已產出", usage_hint="h3"))
    children.append(headline_id)

    filename_id = cid("csv_filename")
    components.append(_text(filename_id, filename, usage_hint="caption"))
    children.append(filename_id)

    rows_id = cid("csv_rows")
    components.append(_text(rows_id, f"共 {row_count} 列", usage_hint="caption"))
    children.append(rows_id)

    url_text_id = cid("csv_url")
    components.append(_text(url_text_id, url, usage_hint="caption"))
    children.append(url_text_id)

    col_id = cid("csv_col")
    components.append(_column(col_id, children, alignment="stretch"))
    components.append(_card("root", col_id))

    return [
        _begin_rendering_envelope(surface_id, "root"),
        _surface_update_envelope(surface_id, components),
    ]


def error_to_a2ui(message: str, surface_id: str = "bqca_error") -> list[dict]:
    cid = _IdGen()
    text_id = cid("err_text")
    col_id = cid("err_col")
    components = [
        _text(text_id, f"⚠️ {message}"),
        _column(col_id, [text_id], alignment="stretch"),
        _card("root", col_id),
    ]
    return [
        _begin_rendering_envelope(surface_id, "root"),
        _surface_update_envelope(surface_id, components),
    ]


_MARKDOWN_TABLE_ROW_LIMIT = 25


def _md_cell(v: Any) -> str:
    s = "" if v is None else str(v)
    return s.replace("|", "\\|").replace("\n", " ")


def _table_to_markdown(payload: dict[str, Any], row_limit: int) -> str:
    headers = payload["headers"]
    rows = payload["rows"]
    total = len(rows)
    shown = rows[:row_limit]
    md = "| " + " | ".join(str(h) for h in headers) + " |\n"
    md += "| " + " | ".join("---" for _ in headers) + " |\n"
    for row in shown:
        md += "| " + " | ".join(_md_cell(c) for c in row) + " |\n"
    if total > row_limit:
        md += (
            f"\n_僅顯示前 {row_limit} 列（共 {total} 列）。"
            "請按下方「匯出此結果為 CSV」取完整資料。_"
        )
    else:
        md += f"\n_共 {total} 列_"
    return md


def bqca_markdown_parts(
    steps: list[dict[str, Any]], table_row_limit: int = _MARKDOWN_TABLE_ROW_LIMIT
) -> list[types.Part]:
    """Convert static-display BQCA steps (SQL, table, answer, …) into one
    native markdown text Part for GE to render.

    GE renders ```sql code blocks with syntax highlight and pipe tables with
    proper layout — better than the A2UI Text/Table primitives. Interactive
    content (chart image, follow-up buttons, clarification picker, CSV
    export) stays in A2UI via `bqca_steps_to_a2ui`.
    """
    chunks: list[str] = []
    for step in steps:
        for kind, payload in step.items():
            if kind == "SQL Generated":
                chunks.append(f"```sql\n{payload}\n```")
            elif kind == "Data Retrieved":
                chunks.append(_table_to_markdown(payload, table_row_limit))
            elif kind == "Answer":
                chunks.append(payload)
    if not chunks:
        return []
    return [types.Part(text="\n\n".join(chunks))]


def bqca_thoughts(steps: list[dict[str, Any]]) -> list[str]:
    """Extract BQCA THOUGHT messages, in arrival order.

    Used by `agent.py` to emit them as native genai Part(thought=True) so
    that Gemini Enterprise renders them in its built-in "思考過程" disclosure
    instead of inline in the chat.
    """
    return [step["Thinking"] for step in steps if "Thinking" in step]


def thought_parts(thoughts: list[str]) -> list[types.Part]:
    """Wrap each thought text as a genai Part flagged thought=True.

    ADK's part_converter turns these into A2A TextParts with
    `metadata={"adk_thought": true}`, which GE recognizes and renders in
    the native thinking-process section.
    """
    return [types.Part(text=t, thought=True) for t in thoughts]


def envelopes_to_parts(envelopes: list[dict]) -> list[types.Part]:
    """Wrap each A2UI envelope so ADK's part_converter emits a real A2A DataPart.

    The default genai→A2A path turns inline_data into a FilePart, which GE shows
    as 'Unsupported attachment'. The only way to get a DataPart out is the sentinel
    trick in google.adk.a2a.converters.part_converter: text/plain inline_data
    wrapped in `<a2a_datapart_json>...</a2a_datapart_json>`, whose body is a
    JSON-serialized a2a_types.DataPart (kind=data, data, metadata).
    """
    parts: list[types.Part] = []
    for env in envelopes:
        datapart_json = json.dumps(
            {
                "kind": "data",
                "data": env,
                "metadata": {"mimeType": A2UI_MIME},
            },
            ensure_ascii=False,
        ).encode("utf-8")
        wrapped = _ADK_DATAPART_OPEN + datapart_json + _ADK_DATAPART_CLOSE
        parts.append(
            types.Part(
                inline_data=types.Blob(
                    data=wrapped,
                    mime_type=_ADK_DATAPART_TEXT_MIME,
                )
            )
        )
    return parts
