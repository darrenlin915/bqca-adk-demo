# ruff: noqa
"""Custom ask_bqca tool with OAuth credential negotiation.

Replaces the ADK DataAgentToolset because we need to support two key formats
for the GE-injected token (`<AUTH_ID>` AND `temp:<AUTH_ID>`) and fall back
to driving the OAuth consent flow during local dev.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import os
import re
from datetime import datetime
from typing import Any

import google.auth
from google.adk.tools import ToolContext
from google.auth.transport.requests import Request
from google.cloud import geminidataanalytics
from google.oauth2.credentials import Credentials
from google.protobuf.json_format import MessageToDict
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

from app import auths

logger = logging.getLogger(__name__)

_DATA_AGENT_PROJECT = os.environ.get("BQCA_DATA_AGENT_PROJECT", "")
_DATA_AGENT_LOCATION = os.environ.get("BQCA_DATA_AGENT_LOCATION", "global")
_DATA_AGENT_ID = os.environ.get("BQCA_DATA_AGENT_ID", "")

BQCA_DATA_AGENT = (
    f"projects/{_DATA_AGENT_PROJECT}/locations/{_DATA_AGENT_LOCATION}"
    f"/dataAgents/{_DATA_AGENT_ID}"
)


def _negotiate_creds(tool_context: ToolContext) -> Credentials | dict:
    """3-stage credential resolution: cached → ADK OAuth response → request flow."""
    # --- Stage 0: ADC fallback for local testing (opt-in via env var) ---
    if os.environ.get("USE_ADC", "").lower() in ("true", "1", "yes"):
        try:
            adc_creds, _ = google.auth.default(scopes=list(auths.SCOPES.keys()))
            if adc_creds.expired and adc_creds.refresh_token:
                adc_creds.refresh(Request())
            return adc_creds
        except Exception as e:
            logger.warning("ADC fallback failed, falling through to OAuth: %s", e)

    # --- Stage 1: cached or GE-injected token ---
    # GE may inject under either bare key or "temp:" prefixed key depending on
    # platform version — check both.
    cached = tool_context.state.get(auths.TOKEN_CACHE_KEY) or tool_context.state.get(
        f"temp:{auths.TOKEN_CACHE_KEY}"
    )
    if cached:
        if isinstance(cached, str):
            return Credentials(token=cached)
        if isinstance(cached, dict):
            try:
                creds = Credentials.from_authorized_user_info(
                    cached, list(auths.SCOPES.keys())
                )
                if creds.valid:
                    return creds
                if creds.expired and creds.refresh_token:
                    creds.refresh(Request())
                    tool_context.state[auths.TOKEN_CACHE_KEY] = json.loads(
                        creds.to_json()
                    )
                    return creds
            except Exception as e:
                logger.warning(f"Cached creds unusable, falling through: {e}")

    # --- Stage 2: response from in-flight ADK OAuth flow (local dev) ---
    if exchanged := tool_context.get_auth_response(auths.AUTH_CONFIG):
        scheme = auths.AUTH_CONFIG.auth_scheme
        cred = auths.AUTH_CONFIG.raw_auth_credential
        creds = Credentials(
            token=exchanged.oauth2.access_token,
            refresh_token=exchanged.oauth2.refresh_token,
            token_uri=scheme.flows.authorizationCode.tokenUrl,
            client_id=cred.oauth2.client_id,
            client_secret=cred.oauth2.client_secret,
            scopes=list(scheme.flows.authorizationCode.scopes.keys()),
        )
        tool_context.state[auths.TOKEN_CACHE_KEY] = json.loads(creds.to_json())
        return creds

    # If client_id is missing, we are in production (Agent Runtime) and the user
    # has not authorized yet. Return PENDING_AUTH instead of attempting to trigger
    # local credential request (which would crash due to empty credentials).

    # --- Stage 3: ask the user to authenticate (local dev only) ---
    tool_context.request_credential(auths.AUTH_CONFIG)
    return {"pending": True, "message": "Awaiting user authentication"}



def _format_messages(stream) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = []
    for msg in stream:
        sm = msg.system_message
        if "text" in sm:
            text = "\n\n".join(sm.text.parts)
            # BQCA TextType mapping:
            #   1 FINAL_RESPONSE        → Answer (user-facing body)
            #   2 THOUGHT               → Thinking (routed to GE thought panel)
            #   3 PROGRESS / 0 UNSPEC   → drop (mirror of SQL/Data/Chart noise)
            #   4 suggested-follow-ups  → Suggested Queries (one Q per line)
            # Value 4 has no SDK enum name in google-cloud-geminidataanalytics
            # v0.12.0 — the SDK emits `UserWarning: Unrecognized TextType enum
            # value: 4`. Compare via int literal until a release names it.
            tt_int = int(sm.text.text_type)
            TT = geminidataanalytics.TextMessage.TextType
            if tt_int == TT.THOUGHT:
                steps.append({"Thinking": text})
            elif tt_int == TT.FINAL_RESPONSE:
                steps.append({"Answer": text})
            elif tt_int == 4:
                qs = [line.strip() for line in text.splitlines() if line.strip()]
                if qs:
                    steps.append({"Suggested Queries": qs})
            else:
                logger.debug("Dropping text message with text_type=%s", tt_int)
        elif "schema" in sm:
            if "query" in sm.schema:
                steps.append({"Question": sm.schema.query.question})
            elif "result" in sm.schema:
                refs = [
                    f"{ds.bigquery_table_reference.project_id}."
                    f"{ds.bigquery_table_reference.dataset_id}."
                    f"{ds.bigquery_table_reference.table_id}"
                    for ds in sm.schema.result.datasources
                ]
                steps.append({"Schema Resolved": refs})
        elif "data" in sm:
            if "generated_sql" in sm.data:
                steps.append({"SQL Generated": sm.data.generated_sql})
            elif "result" in sm.data:
                schema = sm.data.result.schema
                headers = [f.name for f in schema.fields]
                rows = [[row.get(h) for h in headers] for row in list(sm.data.result.data)]
                steps.append({
                    "Data Retrieved": {
                        "headers": headers,
                        "rows": rows,
                        "summary": f"Showing all {len(rows)} rows.",
                    }
                })
        elif "chart" in sm:
            if "query" in sm.chart:
                steps.append({"Chart Requested": sm.chart.query.instructions})
            elif "result" in sm.chart and "vega_config" in sm.chart.result:
                # vega_config is a google.protobuf.Struct. proto-plus exposes it
                # as a MapComposite; .vega_config._pb gives the underlying fields
                # MapContainer (no DESCRIPTOR). Reach Struct via the parent's _pb.
                # We deliberately do NOT render PNG here: keeping the spec (small
                # JSON) in the function_response avoids bloating conversation
                # history with base64 image bytes on every subsequent turn.
                spec = MessageToDict(sm.chart.result._pb.vega_config)
                steps.append({"Chart Spec": spec})
        elif "error" in sm:
            steps.append({"Error": sm.error.message})
        elif "example_queries" in sm:
            qs = [
                eq.natural_language_question
                for eq in sm.example_queries.example_queries
                if eq.natural_language_question
            ]
            if qs:
                steps.append({"Suggested Queries": qs})
        elif "clarification" in sm:
            steps.append({
                "Clarification": [
                    {
                        "question": q.question,
                        "mode": geminidataanalytics.ClarificationQuestion.SelectionMode(
                            q.selection_mode
                        ).name,
                        "options": list(q.options),
                    }
                    for q in sm.clarification.questions
                ]
            })
    return steps


# Cap rows in the LLM-visible response so the function_response in conversation
# history doesn't blow past the model's input token limit on later turns. The
# A2UI table only shows 10 rows anyway, and CSV export reads from state which
# keeps the full row set.
_LLM_MAX_ROWS = 50


def _truncate_steps_for_llm(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for step in steps:
        new_step: dict[str, Any] = {}
        for kind, payload in step.items():
            if (
                kind == "Data Retrieved"
                and isinstance(payload, dict)
                and len(payload.get("rows", [])) > _LLM_MAX_ROWS
            ):
                total = len(payload["rows"])
                new_step[kind] = {
                    **payload,
                    "rows": payload["rows"][:_LLM_MAX_ROWS],
                    "summary": f"Showing first {_LLM_MAX_ROWS} of {total} rows.",
                }
            else:
                new_step[kind] = payload
        out.append(new_step)
    return out


def ask_bqca(question: str, tool_context: ToolContext) -> dict:
    """Query the ApexZenith Games VIP 智能洞察助手 (BigQuery Conversational Analytics agent).

    Use this for ANY question about VIP players, retention, churn, risk
    players, profit/loss, betting volume, or the vip_behavior_mock table.

    Args:
        question: The user's question, in natural language (Chinese or English).
                  Include any context from prior turns to disambiguate.

    Returns:
        A dict with `status` ("SUCCESS", "ERROR", or "PENDING_AUTH") and
        `response` (a list of step dicts: Question, SQL Generated, Data
        Retrieved, Answer).
    """
    if not _DATA_AGENT_PROJECT or not _DATA_AGENT_ID:
        return {
            "status": "ERROR",
            "error_details": (
                "BQCA_DATA_AGENT_PROJECT and BQCA_DATA_AGENT_ID env vars must "
                "be set to call the BQCA data agent."
            ),
        }

    creds = _negotiate_creds(tool_context)
    if isinstance(creds, dict):
        return {"status": "PENDING_AUTH", **creds}

    try:
        client = geminidataanalytics.DataChatServiceClient(credentials=creds)
        request = geminidataanalytics.ChatRequest(
            parent=f"projects/{_DATA_AGENT_PROJECT}/locations/{_DATA_AGENT_LOCATION}",
            messages=[
                geminidataanalytics.Message(
                    user_message=geminidataanalytics.UserMessage(text=question)
                )
            ],
            data_agent_context=geminidataanalytics.DataAgentContext(
                data_agent=BQCA_DATA_AGENT,
                context_version=(
                    geminidataanalytics.DataAgentContext.ContextVersion.PUBLISHED
                ),
            ),
        )
        steps = _format_messages(client.chat(request=request))
        # One-line summary of what BQCA returned so we can tell from logs
        # whether SQL/Data/Chart messages actually arrived for a given query.
        logger.info(
            "ask_bqca step kinds: %s",
            [next(iter(s.keys())) for s in steps],
        )
        # Cache the full payload (all rows) for create_bqca_report (slides.py)
        # and export_bqca_csv to read. The returned response truncates rows so
        # the function_response sitting in conversation history doesn't blow
        # past the LLM input token limit on later turns.
        tool_context.state["last_bqca_payload"] = {
            "question": question,
            "steps": steps,
        }
        return {"status": "SUCCESS", "response": _truncate_steps_for_llm(steps)}
    except Exception:
        logger.exception("ask_bqca failed")
        return {
            "status": "ERROR",
            "error_details": "查詢失敗，請稍後再試。詳細錯誤已記錄於伺服器日誌。",
        }


def export_bqca_csv(tool_context: ToolContext) -> dict:
    """匯出最近一次 ask_bqca 的查詢結果為 CSV，上傳到 Google Drive 並回傳下載連結。

    讀取 `state["last_bqca_payload"]`，將所有 Data Retrieved 步驟的資料表組成
    單一 CSV（多表時以 `# Table N` 分隔列），上傳到使用者的 Drive 後，把連結
    交給 UI 渲染下載按鈕。OAuth 沿用 ask_bqca / create_bqca_report 同一組 creds。

    Returns:
        dict with `status` ("SUCCESS", "ERROR", or "PENDING_AUTH"):
        - SUCCESS: {"url", "filename", "row_count"}
        - PENDING_AUTH: {"message"}
        - ERROR: {"error_details"}
    """
    payload = tool_context.state.get("last_bqca_payload")
    if not payload:
        return {
            "status": "ERROR",
            "error_details": "尚無 BQCA 查詢結果可匯出，請先詢問一個 VIP 分析問題。",
        }

    tables = [
        s["Data Retrieved"]
        for s in payload["steps"]
        if "Data Retrieved" in s and s["Data Retrieved"].get("rows")
    ]
    if not tables:
        return {"status": "ERROR", "error_details": "上次查詢沒有資料表結果可匯出。"}

    creds = _negotiate_creds(tool_context)
    if isinstance(creds, dict):
        return {"status": "PENDING_AUTH", **creds}

    buf = io.StringIO()
    writer = csv.writer(buf)
    row_count = 0
    for i, t in enumerate(tables):
        if i:
            writer.writerow([])
            writer.writerow([f"# Table {i + 1}"])
        writer.writerow(t["headers"])
        writer.writerows(t["rows"])
        row_count += len(t["rows"])
    # utf-8-sig: Excel on Windows mis-decodes CJK headers without the BOM.
    csv_bytes = buf.getvalue().encode("utf-8-sig")

    slug = re.sub(r"[^\w\-]+", "-", payload["question"], flags=re.UNICODE)[:40].strip("-") or "bqca"
    filename = f"bqca-{slug}-{datetime.now():%Y%m%d-%H%M%S}.csv"

    try:
        drive = build("drive", "v3", credentials=creds, cache_discovery=False)
        media = MediaIoBaseUpload(io.BytesIO(csv_bytes), mimetype="text/csv")
        f = drive.files().create(
            body={"name": filename, "mimeType": "text/csv"},
            media_body=media,
            fields="id",
        ).execute()
        # Deliberately no anyone-with-link permission: the file lives in the
        # authenticated user's own Drive, so they can already view/download it
        # via the Drive UI. Public sharing would expose VIP data to anyone who
        # guesses the URL.
        return {
            "status": "SUCCESS",
            "url": f"https://drive.google.com/file/d/{f['id']}/view",
            "filename": filename,
            "row_count": row_count,
        }
    except Exception:
        logger.exception("export_bqca_csv failed")
        return {
            "status": "ERROR",
            "error_details": "CSV 匯出失敗，請稍後再試。詳細錯誤已記錄於伺服器日誌。",
        }
