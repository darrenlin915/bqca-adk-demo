# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0

"""Tests for the BQCA → A2UI v0.9 envelope converter (app/a2ui.py)."""

from __future__ import annotations

import json

import pytest

from app.a2ui import (
    A2UI_MIME,
    A2UI_VERSION,
    BASIC_CATALOG_ID,
    bqca_steps_to_a2ui,
    csv_to_a2ui,
    envelopes_to_parts,
    error_to_a2ui,
    slides_to_a2ui,
)


def _by_id(components: list[dict]) -> dict[str, dict]:
    return {c["id"]: c for c in components}


def _components(envelopes: list[dict]) -> list[dict]:
    """Extract the components list from the updateComponents envelope."""
    for env in envelopes:
        if "updateComponents" in env:
            return env["updateComponents"]["components"]
    return []


# ---- Envelope shape ----

def test_empty_steps_yields_empty_envelopes():
    assert bqca_steps_to_a2ui([]) == []


def test_envelopes_open_with_create_surface_then_update():
    steps = [{"Answer": "hi"}]
    envs = bqca_steps_to_a2ui(steps, surface_id="s1")
    assert len(envs) == 2
    assert envs[0] == {
        "version": A2UI_VERSION,
        "createSurface": {"surfaceId": "s1", "catalogId": BASIC_CATALOG_ID},
    }
    assert envs[1]["version"] == A2UI_VERSION
    assert envs[1]["updateComponents"]["surfaceId"] == "s1"


def test_root_component_present():
    envs = bqca_steps_to_a2ui([{"Answer": "hi"}])
    comps = _components(envs)
    assert any(c["id"] == "root" for c in comps)


# ---- Per-step components ----

def test_answer_step_renders_text():
    envs = bqca_steps_to_a2ui([{"Answer": "VIP 流失率上升 12%"}])
    comps = _components(envs)
    texts = [c for c in comps if c["component"] == "Text"]
    assert any(c["text"] == "VIP 流失率上升 12%" for c in texts)


def test_sql_step_renders_card_with_text():
    envs = bqca_steps_to_a2ui([{"SQL Generated": "SELECT 1"}])
    comps = _components(envs)
    by_id = _by_id(comps)
    cards = [c for c in comps if c["component"] == "Card"]
    assert cards, "expected at least one Card"
    card_child = by_id[cards[0]["child"]]
    assert card_child["component"] == "Text"
    assert "SELECT 1" in card_child["text"]


def test_data_retrieved_renders_table_card_with_header_and_rows():
    step = {
        "Data Retrieved": {
            "headers": ["player_id", "tier"],
            "rows": [["A1", "gold"], ["B2", "silver"]],
            "summary": "Showing all 2 rows.",
        }
    }
    envs = bqca_steps_to_a2ui([step])
    comps = _components(envs)
    cells = [c for c in comps if c["component"] == "Text"]
    cell_texts = {c["text"] for c in cells}
    assert "player_id" in cell_texts
    assert "tier" in cell_texts
    assert "A1" in cell_texts
    assert "silver" in cell_texts


def test_data_retrieved_caps_visible_rows():
    headers = ["x"]
    rows = [[i] for i in range(25)]
    step = {
        "Data Retrieved": {
            "headers": headers,
            "rows": rows,
            "summary": "Showing first 25 of 25 rows.",
        }
    }
    envs = bqca_steps_to_a2ui([step])
    comps = _components(envs)
    cell_texts = [c["text"] for c in comps if c["component"] == "Text"]
    assert any("顯示前" in t for t in cell_texts), (
        "expected a truncation caption when rows exceed MAX_TABLE_ROWS"
    )


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
    comps = _components(envs)
    csv_btns = [
        c
        for c in comps
        if c["component"] == "Button"
        and c.get("action", {}).get("event", {}).get("name") == "send_user_message"
        and c["action"]["event"]["context"].get("text")
        == "請匯出剛剛的查詢結果為 CSV"
    ]
    assert len(csv_btns) == 1, "expected exactly one CSV-export button per table"


def test_chart_spec_renders_image_with_data_url():
    # Minimal valid Vega-Lite spec.
    spec = {
        "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
        "data": {"values": [{"a": 1, "b": 2}]},
        "mark": "bar",
        "encoding": {"x": {"field": "a", "type": "quantitative"}},
    }
    envs = bqca_steps_to_a2ui([{"Chart Spec": spec}])
    comps = _components(envs)
    images = [c for c in comps if c["component"] == "Image"]
    assert images, "expected an Image component for the rendered chart"
    assert images[0]["url"].startswith("data:image/png;base64,")


def test_suggested_queries_become_buttons_with_send_message_action():
    qs = ["上週 VIP 流失人數?", "Top 10 押注 VIP 名單"]
    envs = bqca_steps_to_a2ui([{"Suggested Queries": qs}])
    comps = _components(envs)
    buttons = [c for c in comps if c["component"] == "Button"]
    assert len(buttons) == len(qs)
    for btn, q in zip(buttons, qs):
        assert btn["action"]["event"]["name"] == "send_user_message"
        assert btn["action"]["event"]["context"]["text"] == q


def test_clarification_renders_choicepicker():
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
    pickers = [c for c in comps if c["component"] == "ChoicePicker"]
    assert len(pickers) == 1
    p = pickers[0]
    assert p["variant"] == "mutuallyExclusive"
    assert p["options"] == [
        {"label": "過去 7 天", "value": "過去 7 天"},
        {"label": "過去 30 天", "value": "過去 30 天"},
    ]
    # Submit button must be present and emit a submit_clarification event.
    submit = [
        c
        for c in comps
        if c["component"] == "Button"
        and c.get("action", {}).get("event", {}).get("name") == "submit_clarification"
    ]
    assert len(submit) == 1


def test_clarification_multi_select_maps_to_multiple_selection_variant():
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
    pickers = [c for c in _components(envs) if c["component"] == "ChoicePicker"]
    assert pickers[0]["variant"] == "multipleSelection"


def test_thinking_step_is_dropped():
    envs = bqca_steps_to_a2ui([{"Thinking": "internal"}, {"Answer": "shown"}])
    comps = _components(envs)
    text_blobs = [c["text"] for c in comps if c["component"] == "Text"]
    assert "internal" not in text_blobs
    assert "shown" in text_blobs


# ---- Slides + error ----

def test_slides_envelope_has_open_url_button_and_thumbnail():
    # 1x1 transparent PNG.
    import base64

    px = base64.b64encode(
        bytes.fromhex(
            "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
            "890000000d49444154789c6300010000000500010d0a2db40000000049454e44ae426082"
        )
    ).decode()

    envs = slides_to_a2ui("https://docs.google.com/presentation/d/abc", [px])
    comps = _components(envs)
    by_id = _by_id(comps)
    # Open button: primary, openUrl functionCall with our URL.
    open_btns = [
        c
        for c in comps
        if c["component"] == "Button"
        and c.get("action", {}).get("functionCall", {}).get("call") == "openUrl"
    ]
    assert len(open_btns) == 1
    assert (
        open_btns[0]["action"]["functionCall"]["args"]["url"]
        == "https://docs.google.com/presentation/d/abc"
    )
    # Thumbnail image present.
    imgs = [c for c in comps if c["component"] == "Image"]
    assert imgs
    assert imgs[0]["url"].startswith("data:image/png;base64,")
    # Root is the Card.
    assert by_id["root"]["component"] == "Card"


def test_csv_envelope_has_download_button_and_metadata():
    envs = csv_to_a2ui(
        "https://drive.google.com/uc?export=download&id=XYZ",
        "bqca-test-20260519-120000.csv",
        row_count=42,
    )
    comps = _components(envs)
    by_id = _by_id(comps)
    dl_btns = [
        c
        for c in comps
        if c["component"] == "Button"
        and c.get("action", {}).get("functionCall", {}).get("call") == "openUrl"
    ]
    assert len(dl_btns) == 1
    assert (
        dl_btns[0]["action"]["functionCall"]["args"]["url"]
        == "https://drive.google.com/uc?export=download&id=XYZ"
    )
    text_blobs = [c["text"] for c in comps if c["component"] == "Text"]
    assert "bqca-test-20260519-120000.csv" in text_blobs
    assert any("共 42 列" in t for t in text_blobs)
    assert by_id["root"]["component"] == "Card"


def test_error_envelope_shape():
    envs = error_to_a2ui("授權過期", surface_id="bqca_auth")
    assert envs[0]["createSurface"]["surfaceId"] == "bqca_auth"
    comps = _components(envs)
    texts = [c["text"] for c in comps if c["component"] == "Text"]
    assert any("授權過期" in t for t in texts)


# ---- envelopes_to_parts ----

def test_envelopes_to_parts_uses_a2ui_mime_type_per_part():
    envs = bqca_steps_to_a2ui([{"Answer": "hi"}])
    parts = envelopes_to_parts(envs)
    assert len(parts) == len(envs)
    for part, env in zip(parts, envs):
        assert part.inline_data.mime_type == A2UI_MIME
        # Round-trip the JSON.
        assert json.loads(part.inline_data.data.decode("utf-8")) == env
