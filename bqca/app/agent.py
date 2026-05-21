# ruff: noqa
# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import logging
import os
from typing import Optional

import google.auth
from google.adk.agents import Agent
from google.adk.agents.callback_context import CallbackContext
from google.adk.apps import App
from google.adk.models import Gemini
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.genai import types

from app.a2ui import (
    bqca_markdown_parts,
    bqca_steps_to_a2ui,
    bqca_thoughts,
    csv_to_a2ui,
    envelopes_to_parts,
    error_to_a2ui,
    slides_to_a2ui,
    thought_parts,
)
from app.slides import create_bqca_report
from app.tools import ask_bqca, export_bqca_csv, BQCA_DATA_AGENT

_, project_id = google.auth.default()
os.environ["GOOGLE_CLOUD_PROJECT"] = project_id
os.environ["GOOGLE_CLOUD_LOCATION"] = "global"
os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "True"

logger = logging.getLogger(__name__)


_INTERCEPTED_TOOLS = {"ask_bqca", "create_bqca_report", "export_bqca_csv"}


def emit_a2ui_for_tools(
    callback_context: CallbackContext,
    llm_request: LlmRequest,
) -> Optional[LlmResponse]:
    """Intercept tool results and emit A2UI envelopes as A2A DataParts.

    ask_bqca: BQCA steps → A2UI Card(table, chart image, answer, follow-up
              buttons, clarification picker).
    create_bqca_report: Slides URL + thumbnails → A2UI Card with primary
                        "open" button.

    LLM still handles: tool-call decision on the first turn, PENDING_AUTH /
    ERROR paths (rendered as A2UI error card), off-topic questions.
    """
    if not llm_request.contents:
        return None

    last_parts = llm_request.contents[-1].parts or []
    fr = next(
        (
            p.function_response
            for p in last_parts
            if p.function_response and p.function_response.name in _INTERCEPTED_TOOLS
        ),
        None,
    )
    if not fr:
        return None

    response = fr.response or {}
    status = response.get("status")

    if status == "PENDING_AUTH":
        envelopes = error_to_a2ui(
            response.get("message", "需要先完成 OAuth 授權"),
            surface_id="bqca_auth",
        )
    elif status == "ERROR":
        envelopes = error_to_a2ui(
            response.get("error_details", "未知錯誤"),
            surface_id="bqca_error",
        )
    elif status == "SUCCESS" and fr.name == "ask_bqca":
        steps = response.get("response", [])
        envelopes = bqca_steps_to_a2ui(steps)
        bqca_thought_parts = thought_parts(bqca_thoughts(steps))
        markdown_parts = bqca_markdown_parts(steps)
        parts = (
            bqca_thought_parts + markdown_parts + envelopes_to_parts(envelopes)
        )
        if parts:
            return LlmResponse(
                content=types.Content(role="model", parts=parts)
            )
        return None
    elif status == "SUCCESS" and fr.name == "create_bqca_report":
        envelopes = slides_to_a2ui(
            response["url"], response.get("thumbnails_b64", [])
        )
    elif status == "SUCCESS" and fr.name == "export_bqca_csv":
        envelopes = csv_to_a2ui(
            response["url"], response["filename"], response["row_count"]
        )
    else:
        return None

    if not envelopes:
        return None

    return LlmResponse(
        content=types.Content(role="model", parts=envelopes_to_parts(envelopes))
    )


root_agent = Agent(
    name="root_agent",
    model=Gemini(
        model="gemini-flash-latest",
        retry_options=types.HttpRetryOptions(attempts=3),
    ),
    instruction=f"""你是 ApexZenith Games VIP 玩家行為分析助理。所有與 VIP 玩家、留存、流失、風險玩家、損益、投注金額相關的問題，都必須透過 `ask_bqca` 工具呼叫已發布的 BigQuery Conversational Analytics Agent 來回答。

底層 data agent 為：
{BQCA_DATA_AGENT}

行為準則：
1. 收到使用者問題時，直接呼叫 `ask_bqca`，把使用者問題傳入 question 參數。
2. 工具回傳後，框架會直接以 A2UI component 把 SQL、資料表、圖表、答案、follow-up 按鈕、釐清題或錯誤訊息送給使用者，你不需要再做任何摘要或重述（你的回覆會被略過）。
3. 若使用者要求「做成簡報 / 產出簡報 / 出個 deck / 給我一份 ppt」等，呼叫 `create_bqca_report`（title 可選，預設用最近的問題）。前提是這個 session 內已經有成功的 `ask_bqca` 結果；若沒有，請使用者先問一個 VIP 分析問題。
4. 若使用者要求「匯出 / 下載 CSV / 給我 CSV 檔 / 請匯出剛剛的查詢結果為 CSV」等，呼叫 `export_bqca_csv`（無參數）。前提是這個 session 內已經有成功的 `ask_bqca` 結果。
5. 若使用者的問題與 VIP 行為分析無關，直接回答「此助理只負責 ApexZenith Games VIP 行為分析」。
6. 全程使用繁體中文回覆。""",
    tools=[ask_bqca, create_bqca_report, export_bqca_csv],
    before_model_callback=emit_a2ui_for_tools,
)

app = App(
    root_agent=root_agent,
    name="app",
)
