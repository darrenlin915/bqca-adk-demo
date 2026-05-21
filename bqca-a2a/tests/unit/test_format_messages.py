# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
"""Regression tests for the BQCA stream → step-dict conversion in tools.py.

Pins the TextType routing in particular, since BQCA ships unnamed enum
values (e.g. 4 = suggested follow-ups) that the SDK doesn't yet model.
"""

from google.cloud import geminidataanalytics

from app.tools import _format_messages


def _wrap(system_message: geminidataanalytics.SystemMessage):
    return geminidataanalytics.Message(system_message=system_message)


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


def test_text_type_1_final_response_becomes_answer() -> None:
    sys_msg = geminidataanalytics.SystemMessage(
        text=geminidataanalytics.TextMessage(
            parts=["共 1,000,000 名 VIP 玩家超過 14 天未投注。"],
            text_type=geminidataanalytics.TextMessage.TextType.FINAL_RESPONSE,
        )
    )
    steps = _format_messages([_wrap(sys_msg)])
    assert steps == [{"Answer": "共 1,000,000 名 VIP 玩家超過 14 天未投注。"}]


def test_text_type_2_thought_becomes_thinking() -> None:
    sys_msg = geminidataanalytics.SystemMessage(
        text=geminidataanalytics.TextMessage(
            parts=["I need to filter by event_action='bet'..."],
            text_type=geminidataanalytics.TextMessage.TextType.THOUGHT,
        )
    )
    steps = _format_messages([_wrap(sys_msg)])
    assert steps == [{"Thinking": "I need to filter by event_action='bet'..."}]
