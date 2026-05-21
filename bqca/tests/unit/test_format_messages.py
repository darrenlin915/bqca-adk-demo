# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
"""Regression tests for the BQCA stream → step-dict conversion in tools.py.

Specifically pins the chart-result extraction path: vega_config is a
google.protobuf.Struct exposed by proto-plus as a MapComposite. Calling
MessageToDict on `chart.result.vega_config._pb` raises (it returns the
fields MapContainer, not the Struct). The correct access is via the
parent's _pb. This test would have caught that bug pre-deploy.
"""

from google.cloud import geminidataanalytics
from google.protobuf import struct_pb2

from app.tools import _LLM_MAX_ROWS, _format_messages, _truncate_steps_for_llm


def _wrap(system_message: geminidataanalytics.SystemMessage):
    """Mimic the Message shape the BQCA chat stream yields."""
    return geminidataanalytics.Message(system_message=system_message)


def test_format_messages_extracts_chart_vega_spec_as_dict() -> None:
    spec = struct_pb2.Struct()
    spec["$schema"] = "https://vega.github.io/schema/vega-lite/v5.json"
    spec["mark"] = "bar"

    chart_msg = geminidataanalytics.ChartMessage(
        result=geminidataanalytics.ChartResult(vega_config=spec)
    )
    sys_msg = geminidataanalytics.SystemMessage(chart=chart_msg)

    steps = _format_messages([_wrap(sys_msg)])

    assert steps == [
        {
            "Chart Spec": {
                "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
                "mark": "bar",
            }
        }
    ]


def test_format_messages_extracts_chart_query_instructions() -> None:
    chart_msg = geminidataanalytics.ChartMessage(
        query=geminidataanalytics.ChartQuery(instructions="畫成長條圖")
    )
    sys_msg = geminidataanalytics.SystemMessage(chart=chart_msg)

    steps = _format_messages([_wrap(sys_msg)])
    assert steps == [{"Chart Requested": "畫成長條圖"}]


def test_format_messages_text_branch_unchanged() -> None:
    # Sanity check existing path still works after our changes.
    sys_msg = geminidataanalytics.SystemMessage(
        text=geminidataanalytics.TextMessage(
            parts=["共 ", "123 ", "位"],
            text_type=geminidataanalytics.TextMessage.TextType.FINAL_RESPONSE,
        )
    )
    steps = _format_messages([_wrap(sys_msg)])
    # parts are joined with double-newline (preserves paragraph breaks).
    assert steps == [{"Answer": "共 \n\n123 \n\n位"}]


def test_format_messages_drops_progress_and_unknown_text_types() -> None:
    # PROGRESS (3) and unknown enum values (server may roll out new types
    # ahead of the SDK) should be silently dropped — not mis-labeled as Answer.
    TT = geminidataanalytics.TextMessage.TextType
    msgs = [
        _wrap(geminidataanalytics.SystemMessage(
            text=geminidataanalytics.TextMessage(parts=["working..."], text_type=TT.PROGRESS)
        )),
        _wrap(geminidataanalytics.SystemMessage(
            text=geminidataanalytics.TextMessage(parts=["unknown"], text_type=TT.TEXT_TYPE_UNSPECIFIED)
        )),
    ]
    assert _format_messages(msgs) == []


def test_text_type_4_becomes_suggested_queries() -> None:
    # BQCA emits follow-up suggestions as a TextMessage with text_type=4 —
    # an enum value google-cloud-geminidataanalytics v0.12.0 has no name for.
    # Body is one question per line; blank lines must be dropped.
    body = (
        "這些流失玩家在停滯前最常玩哪些遊戲？\n"
        "預測未來 30 天 VIP 玩家投注額趨勢。\n"
        "\n"
        "  偵測過去三個月每日總損益異常。  \n"
    )
    sys_msg = geminidataanalytics.SystemMessage(
        text=geminidataanalytics.TextMessage(parts=[body], text_type=4)
    )
    steps = _format_messages([_wrap(sys_msg)])
    assert steps == [
        {
            "Suggested Queries": [
                "這些流失玩家在停滯前最常玩哪些遊戲？",
                "預測未來 30 天 VIP 玩家投注額趨勢。",
                "偵測過去三個月每日總損益異常。",
            ]
        }
    ]


def test_format_messages_extracts_example_queries_as_suggested_queries() -> None:
    from google.cloud.geminidataanalytics_v1alpha.types.context import ExampleQuery

    sys_msg = geminidataanalytics.SystemMessage(
        example_queries=geminidataanalytics.ExampleQueries(
            example_queries=[
                ExampleQuery(natural_language_question="上週 VIP 流失人數?"),
                ExampleQuery(natural_language_question="Top 10 押注 VIP 名單"),
                ExampleQuery(natural_language_question=""),  # filtered out
            ]
        )
    )
    steps = _format_messages([_wrap(sys_msg)])
    assert steps == [
        {"Suggested Queries": ["上週 VIP 流失人數?", "Top 10 押注 VIP 名單"]}
    ]


def test_truncate_steps_for_llm_caps_large_data_retrieved() -> None:
    """Large Data Retrieved rows must be capped before going into LLM history,
    otherwise the function_response blows past the model's input token limit."""
    rows = [[i, f"v{i}"] for i in range(_LLM_MAX_ROWS + 100)]
    steps = [
        {"SQL Generated": "SELECT 1"},
        {"Data Retrieved": {"headers": ["x", "y"], "rows": rows, "summary": "..."}},
        {"Answer": "ok"},
    ]
    out = _truncate_steps_for_llm(steps)

    assert out[0] == {"SQL Generated": "SELECT 1"}
    assert out[2] == {"Answer": "ok"}
    dr = out[1]["Data Retrieved"]
    assert len(dr["rows"]) == _LLM_MAX_ROWS
    assert "Showing first" in dr["summary"]
    # Original must NOT be mutated — state still needs full rows for CSV export.
    assert len(steps[1]["Data Retrieved"]["rows"]) == _LLM_MAX_ROWS + 100


def test_truncate_steps_for_llm_passes_small_tables_through() -> None:
    steps = [{"Data Retrieved": {"headers": ["x"], "rows": [[1], [2]], "summary": "s"}}]
    out = _truncate_steps_for_llm(steps)
    assert out == steps


def test_format_messages_extracts_clarification_with_mode_and_options() -> None:
    sys_msg = geminidataanalytics.SystemMessage(
        clarification=geminidataanalytics.ClarificationMessage(
            questions=[
                geminidataanalytics.ClarificationQuestion(
                    question="你想看哪個時段?",
                    selection_mode=(
                        geminidataanalytics.ClarificationQuestion.SelectionMode.SINGLE_SELECT
                    ),
                    options=["過去 7 天", "過去 30 天"],
                )
            ]
        )
    )
    steps = _format_messages([_wrap(sys_msg)])
    assert steps == [
        {
            "Clarification": [
                {
                    "question": "你想看哪個時段?",
                    "mode": "SINGLE_SELECT",
                    "options": ["過去 7 天", "過去 30 天"],
                }
            ]
        }
    ]
