# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0

"""Tests for the BQCA → A2UI v0.8 envelope converter (app/a2ui.py)."""

from __future__ import annotations

import json

from a2a.types import DataPart

from app.a2ui import (
    A2UI_MIME,
    A2UI_VERSION,
    STANDARD_CATALOG_ID,
    bqca_markdown_parts,
    bqca_steps_to_a2ui,
    csv_to_a2ui,
    envelopes_to_parts,
    error_to_a2ui,
    slides_to_a2ui,
)


def _by_id(components: list[dict]) -> dict[str, dict]:
    return {c["id"]: c for c in components}


def _components(envelopes: list[dict]) -> list[dict]:
    """Extract the components list from the surfaceUpdate envelope."""
    for env in envelopes:
        if "surfaceUpdate" in env:
            return env["surfaceUpdate"]["components"]
    return []


def _component_kind(c: dict) -> str:
    """v0.8 discriminator: components are `{"id": ..., "component": {"<Kind>": {...}}}`."""
    return next(iter(c["component"]))


def _component_body(c: dict) -> dict:
    return c["component"][_component_kind(c)]


def _filter(components: list[dict], kind: str) -> list[dict]:
    return [c for c in components if _component_kind(c) == kind]


def _text_of(c: dict) -> str | None:
    """Get literal text from a Text component, or None if it's path-bound."""
    return _component_body(c).get("text", {}).get("literalString")


# ---- Module-level constants ----

def test_module_constants():
    assert A2UI_VERSION == "0.8"
    assert A2UI_MIME == "application/json+a2ui"
    assert STANDARD_CATALOG_ID == (
        "https://a2ui.org/specification/v0_8/standard_catalog_definition.json"
    )


# ---- Envelope shape ----

def test_empty_steps_yields_empty_envelopes():
    assert bqca_steps_to_a2ui([]) == []


def test_envelopes_open_with_begin_rendering_then_surface_update():
    # Suggested Queries still produces A2UI components (Answer / SQL / Data
    # Retrieved table now route through bqca_markdown_parts instead).
    steps = [{"Suggested Queries": ["下週 VIP 流失人數?"]}]
    envs = bqca_steps_to_a2ui(steps, surface_id="s1")
    assert len(envs) == 2
    # beginRendering first per GE reference example pattern.
    assert envs[0] == {
        "beginRendering": {
            "surfaceId": "s1",
            "root": "root",
            "catalogId": STANDARD_CATALOG_ID,
        }
    }
    assert envs[1]["surfaceUpdate"]["surfaceId"] == "s1"


def test_root_component_present():
    envs = bqca_steps_to_a2ui([{"Suggested Queries": ["下週 VIP 流失人數?"]}])
    comps = _components(envs)
    assert any(c["id"] == "root" for c in comps)
    root = _by_id(comps)["root"]
    # Root is a Column wrapping all top-level children.
    assert _component_kind(root) == "Column"


# ---- Per-step components (interactive content only — static SQL / table /
# Answer now flow through bqca_markdown_parts as native markdown) ----

def test_answer_step_is_skipped_in_a2ui_envelopes():
    # Answer is rendered as a native markdown Part, not an A2UI Text component.
    assert bqca_steps_to_a2ui([{"Answer": "VIP 流失率上升 12%"}]) == []


def test_sql_step_is_skipped_in_a2ui_envelopes():
    # SQL Generated flows through bqca_markdown_parts as a fenced ```sql block.
    assert bqca_steps_to_a2ui([{"SQL Generated": "SELECT 1"}]) == []


def test_data_retrieved_table_is_skipped_in_a2ui_envelopes():
    # Data Retrieved table content is rendered as a markdown pipe-table; only
    # the CSV export button stays in A2UI (see next test).
    step = {
        "Data Retrieved": {
            "headers": ["player_id", "tier"],
            "rows": [["A1", "gold"], ["B2", "silver"]],
            "summary": "Showing all 2 rows.",
        }
    }
    cell_texts = {
        _text_of(c) for c in _filter(_components(bqca_steps_to_a2ui([step])), "Text")
    }
    # No header cell or row cell appears as a literal A2UI Text component;
    # the only Text emitted is the CSV button label (asserted separately).
    assert "player_id" not in cell_texts
    assert "A1" not in cell_texts


def test_data_retrieved_card_includes_csv_export_button():
    """The 'Data Retrieved' branch must auto-inject a send_user_message button
    whose text matches the exact phrase agent.py's instruction listens for."""
    step = {
        "Data Retrieved": {
            "headers": ["x"],
            "rows": [[1]],
            "summary": "Showing all 1 rows.",
        }
    }
    envs = bqca_steps_to_a2ui([step])
    expected_ctx = [
        {"key": "text", "value": {"literalString": "請匯出剛剛的查詢結果為 CSV"}}
    ]
    csv_btns = [
        b
        for b in _filter(_components(envs), "Button")
        if _component_body(b)["action"].get("name") == "send_user_message"
        and _component_body(b)["action"].get("context") == expected_ctx
    ]
    assert len(csv_btns) == 1, "expected exactly one CSV-export button per table"


def test_chart_spec_renders_image_with_data_url():
    spec = {
        "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
        "data": {"values": [{"a": 1, "b": 2}]},
        "mark": "bar",
        "encoding": {"x": {"field": "a", "type": "quantitative"}},
    }
    envs = bqca_steps_to_a2ui([{"Chart Spec": spec}])
    imgs = _filter(_components(envs), "Image")
    assert imgs, "expected an Image component for the rendered chart"
    url = _component_body(imgs[0])["url"]["literalString"]
    assert url.startswith("data:image/png;base64,")


def test_suggested_queries_become_buttons_with_send_message_action():
    qs = ["上週 VIP 流失人數?", "Top 10 押注 VIP 名單"]
    envs = bqca_steps_to_a2ui([{"Suggested Queries": qs}])
    buttons = _filter(_components(envs), "Button")
    assert len(buttons) == len(qs)
    for btn, q in zip(buttons, qs):
        action = _component_body(btn)["action"]
        assert action["name"] == "send_user_message"
        assert action["context"] == [
            {"key": "text", "value": {"literalString": q}}
        ]


def test_clarification_renders_multiple_choice():
    step = {
        "Clarification": [
            {
                "question": "你想看哪個時段?",
                "mode": "SINGLE_SELECT",
                "options": ["過去 7 天", "過去 30 天"],
            }
        ]
    }
    envs = bqca_steps_to_a2ui([step])
    comps = _components(envs)
    pickers = _filter(comps, "MultipleChoice")
    assert len(pickers) == 1
    body = _component_body(pickers[0])
    assert body["maxAllowedSelections"] == 1
    assert body["options"] == [
        {"label": {"literalString": "過去 7 天"}, "value": "過去 7 天"},
        {"label": {"literalString": "過去 30 天"}, "value": "過去 30 天"},
    ]
    assert body["selections"] == {"path": "/clarification/0"}
    # Submit button present with submit_clarification action.
    submits = [
        b
        for b in _filter(comps, "Button")
        if _component_body(b)["action"]["name"] == "submit_clarification"
    ]
    assert len(submits) == 1
    assert _component_body(submits[0])["primary"] is True


def test_clarification_multi_select_allows_all_options():
    step = {
        "Clarification": [
            {
                "question": "選一或多個 tier?",
                "mode": "MULTI_SELECT",
                "options": ["gold", "silver", "bronze"],
            }
        ]
    }
    envs = bqca_steps_to_a2ui([step])
    pickers = _filter(_components(envs), "MultipleChoice")
    assert _component_body(pickers[0])["maxAllowedSelections"] == 3


def test_thinking_step_is_excluded_from_a2ui_envelopes():
    # Thoughts are emitted as native Part(thought=True) by agent.py — they
    # must NOT show up inside the A2UI surface (that would duplicate them
    # in the main chat area alongside GE's native thinking disclosure).
    # Pair with a Suggested Queries step so the envelopes list is non-empty.
    envs = bqca_steps_to_a2ui(
        [
            {"Thinking": "我需要先查 vip_behavior_mock"},
            {"Suggested Queries": ["下週 VIP 流失人數?"]},
        ]
    )
    text_blobs = [_text_of(c) for c in _filter(_components(envs), "Text")]
    assert "我需要先查 vip_behavior_mock" not in text_blobs
    # Suggested Queries DOES render a Text label for each button.
    assert "下週 VIP 流失人數?" in text_blobs


def test_bqca_thoughts_extracts_thinking_in_order():
    from app.a2ui import bqca_thoughts

    steps = [
        {"Thinking": "step 1"},
        {"Answer": "ignored"},
        {"Thinking": "step 2"},
        {"SQL Generated": "SELECT 1"},
    ]
    assert bqca_thoughts(steps) == ["step 1", "step 2"]


def test_thought_parts_flag_native_thought_metadata():
    from app.a2ui import thought_parts

    parts = thought_parts(["alpha", "beta"])
    assert len(parts) == 2
    assert all(p.thought is True for p in parts)
    assert [p.text for p in parts] == ["alpha", "beta"]


# ---- bqca_markdown_parts (the native markdown route for SQL / table / Answer) ----

def test_bqca_markdown_parts_emits_sql_table_and_answer_in_one_part():
    steps = [
        {"SQL Generated": "SELECT player_id FROM vip"},
        {
            "Data Retrieved": {
                "headers": ["player_id", "tier"],
                "rows": [["A1", "gold"], ["B2", "silver"]],
                "summary": "Showing all 2 rows.",
            }
        },
        {"Answer": "共 2 位 VIP 玩家"},
    ]
    parts = bqca_markdown_parts(steps)
    assert len(parts) == 1
    text = parts[0].text
    # SQL rendered as a fenced code block so GE applies syntax highlight.
    assert "```sql\nSELECT player_id FROM vip\n```" in text
    # Pipe table header + at least one row cell.
    assert "| player_id | tier |" in text
    assert "| A1 | gold |" in text
    # Answer string is copied through verbatim.
    assert "共 2 位 VIP 玩家" in text


def test_bqca_markdown_parts_caps_table_rows_and_points_to_csv_button():
    big = {
        "Data Retrieved": {
            "headers": ["x"],
            "rows": [[i] for i in range(40)],
            "summary": "Showing all 40 rows.",
        }
    }
    parts = bqca_markdown_parts([big], table_row_limit=25)
    text = parts[0].text
    # Truncation notice mentions the cap and tells the user where the full data lives.
    assert "僅顯示前 25 列" in text
    assert "匯出此結果為 CSV" in text


def test_bqca_markdown_parts_returns_empty_when_no_renderable_steps():
    # Question / Schema Resolved / Chart Spec etc. do not contribute markdown
    # — they're either skipped entirely or rendered via A2UI components.
    assert bqca_markdown_parts([{"Question": "?"}, {"Chart Spec": {}}]) == []


# ---- Slides + error ----

def test_slides_envelope_renders_thumbnail_image_and_url_text():
    # 1x1 transparent PNG.
    px = base64_1x1_png()
    envs = slides_to_a2ui("https://docs.google.com/presentation/d/abc", [px])
    comps = _components(envs)
    by_id = _by_id(comps)
    # Root is a Card (per v0.8 standard catalog).
    assert _component_kind(by_id["root"]) == "Card"
    # Thumbnail Image present and inlined as a data URL.
    imgs = _filter(comps, "Image")
    assert imgs
    assert _component_body(imgs[0])["url"]["literalString"].startswith(
        "data:image/png;base64,"
    )
    # URL is rendered as text (no openUrl in v0.8 standard catalog).
    text_values = [_text_of(c) for c in _filter(comps, "Text")]
    assert "https://docs.google.com/presentation/d/abc" in text_values


def test_csv_envelope_renders_filename_row_count_and_url_text():
    envs = csv_to_a2ui(
        "https://drive.google.com/uc?export=download&id=XYZ",
        "bqca-test-20260519-120000.csv",
        row_count=42,
    )
    comps = _components(envs)
    by_id = _by_id(comps)
    assert _component_kind(by_id["root"]) == "Card"
    text_values = [_text_of(c) for c in _filter(comps, "Text")]
    assert "bqca-test-20260519-120000.csv" in text_values
    assert any(t and "共 42 列" in t for t in text_values)
    # URL rendered as plain text (no openUrl primitive in v0.8 standard catalog).
    assert "https://drive.google.com/uc?export=download&id=XYZ" in text_values


def test_error_envelope_shape():
    envs = error_to_a2ui("授權過期", surface_id="bqca_auth")
    assert envs[0]["beginRendering"]["surfaceId"] == "bqca_auth"
    texts = [_text_of(c) for c in _filter(_components(envs), "Text")]
    assert any(t and "授權過期" in t for t in texts)


# ---- envelopes_to_parts (the part wrapping that GE actually cares about) ----

def test_envelopes_to_parts_wraps_for_adk_datapart_conversion():
    """ADK's part_converter only emits A2A DataPart for inline_data that's
    text/plain AND wrapped in `<a2a_datapart_json>...</a2a_datapart_json>`.
    Anything else becomes a FilePart → 'Unsupported attachment' in GE."""
    envs = bqca_steps_to_a2ui([{"Answer": "hi"}])
    parts = envelopes_to_parts(envs)
    assert len(parts) == len(envs)
    for part, env in zip(parts, envs):
        assert part.inline_data.mime_type == "text/plain"
        data = part.inline_data.data
        assert data.startswith(b"<a2a_datapart_json>")
        assert data.endswith(b"</a2a_datapart_json>")
        inner = data[len(b"<a2a_datapart_json>") : -len(b"</a2a_datapart_json>")]
        # The inner JSON must validate as an a2a_types.DataPart with our envelope.
        dp = DataPart.model_validate_json(inner)
        assert dp.data == env
        assert dp.metadata == {"mimeType": A2UI_MIME}


def base64_1x1_png() -> str:
    import base64
    return base64.b64encode(
        bytes.fromhex(
            "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
            "890000000d49444154789c6300010000000500010d0a2db40000000049454e44ae426082"
        )
    ).decode()
