# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
"""Tests for emit_a2ui_for_tools — the before_model_callback that intercepts
ask_bqca / create_bqca_report tool results and emits A2UI v0.9 envelopes as
A2A DataParts instead of letting the LLM summarize.

Five paths to pin:
  - ask_bqca SUCCESS      → A2UI envelopes (createSurface + updateComponents)
  - create_bqca_report OK → A2UI Card with thumbnail Image + openUrl Button
  - PENDING_AUTH / ERROR  → A2UI error card
  - first turn (no contents) / off-topic / other-tool → None (let LLM handle)
"""

import json
from unittest.mock import MagicMock

from google.adk.agents.callback_context import CallbackContext
from google.adk.models.llm_request import LlmRequest
from google.genai import types

from app.a2ui import A2UI_MIME
from app.agent import emit_a2ui_for_tools


_VEGA_SPEC = {
    "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
    "data": {"values": [{"a": "X", "b": 28}, {"a": "Y", "b": 55}]},
    "mark": "bar",
    "encoding": {
        "x": {"field": "a", "type": "nominal"},
        "y": {"field": "b", "type": "quantitative"},
    },
}


def _function_response(name: str, response: dict) -> types.Content:
    return types.Content(
        role="user",
        parts=[
            types.Part(
                function_response=types.FunctionResponse(name=name, response=response)
            )
        ],
    )


def _ctx() -> CallbackContext:
    return MagicMock(spec=CallbackContext)


def _envelopes_from(resp) -> list[dict]:
    return [json.loads(p.inline_data.data) for p in resp.content.parts]


# ---- SUCCESS paths ----

def test_ask_bqca_success_emits_a2ui_envelopes():
    contents = [
        _function_response(
            "ask_bqca",
            {
                "status": "SUCCESS",
                "response": [
                    {"Answer": "共 123 位"},
                    {"Chart Spec": _VEGA_SPEC},
                    {"Suggested Queries": ["上週 VIP 流失人數?"]},
                ],
            },
        )
    ]
    resp = emit_a2ui_for_tools(_ctx(), LlmRequest(contents=contents))

    assert resp is not None
    assert resp.content is not None
    assert resp.content.role == "model"
    for p in resp.content.parts:
        assert p.inline_data is not None
        assert p.inline_data.mime_type == A2UI_MIME

    envs = _envelopes_from(resp)
    # createSurface first, then updateComponents.
    assert "createSurface" in envs[0]
    assert "updateComponents" in envs[1]


def test_create_bqca_report_success_emits_a2ui_with_open_url_button():
    contents = [
        _function_response(
            "create_bqca_report",
            {
                "status": "SUCCESS",
                "url": "https://docs.google.com/presentation/d/abc",
                "thumbnails_b64": [],
            },
        )
    ]
    resp = emit_a2ui_for_tools(_ctx(), LlmRequest(contents=contents))
    assert resp is not None
    envs = _envelopes_from(resp)
    components = envs[1]["updateComponents"]["components"]
    open_btns = [
        c
        for c in components
        if c["component"] == "Button"
        and c.get("action", {}).get("functionCall", {}).get("call") == "openUrl"
    ]
    assert len(open_btns) == 1
    assert (
        open_btns[0]["action"]["functionCall"]["args"]["url"]
        == "https://docs.google.com/presentation/d/abc"
    )


# ---- Error paths now also flow through the callback (A2UI only) ----

def test_pending_auth_emits_a2ui_error_card():
    contents = [
        _function_response(
            "ask_bqca", {"status": "PENDING_AUTH", "message": "請完成授權"}
        )
    ]
    resp = emit_a2ui_for_tools(_ctx(), LlmRequest(contents=contents))
    assert resp is not None
    envs = _envelopes_from(resp)
    assert envs[0]["createSurface"]["surfaceId"] == "bqca_auth"
    texts = [
        c["text"]
        for c in envs[1]["updateComponents"]["components"]
        if c["component"] == "Text"
    ]
    assert any("請完成授權" in t for t in texts)


def test_error_emits_a2ui_error_card():
    contents = [
        _function_response(
            "ask_bqca", {"status": "ERROR", "error_details": "BQ permission denied"}
        )
    ]
    resp = emit_a2ui_for_tools(_ctx(), LlmRequest(contents=contents))
    assert resp is not None
    envs = _envelopes_from(resp)
    assert envs[0]["createSurface"]["surfaceId"] == "bqca_error"
    texts = [
        c["text"]
        for c in envs[1]["updateComponents"]["components"]
        if c["component"] == "Text"
    ]
    assert any("BQ permission denied" in t for t in texts)


# ---- Pass-through paths (LLM still runs) ----

def test_pass_through_when_no_contents():
    # First turn: LLM must decide to call the tool.
    assert emit_a2ui_for_tools(_ctx(), LlmRequest(contents=[])) is None


def test_pass_through_on_plain_user_message():
    contents = [types.Content(role="user", parts=[types.Part(text="天氣如何？")])]
    assert emit_a2ui_for_tools(_ctx(), LlmRequest(contents=contents)) is None


def test_pass_through_when_function_response_is_from_other_tool():
    contents = [_function_response("some_other_tool", {"status": "SUCCESS"})]
    assert emit_a2ui_for_tools(_ctx(), LlmRequest(contents=contents)) is None
