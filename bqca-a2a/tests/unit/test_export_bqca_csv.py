# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0

"""Tests for export_bqca_csv and _truncate_steps_for_llm in app/tools.py."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from google.oauth2.credentials import Credentials

from app.tools import _LLM_MAX_ROWS, _truncate_steps_for_llm, export_bqca_csv


def _ctx(state: dict | None = None) -> SimpleNamespace:
    return SimpleNamespace(state=state if state is not None else {})


# ---- _truncate_steps_for_llm (guards against repeating the 1.8M-token blow-up) ----

def test_truncate_caps_large_data_retrieved():
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


def test_truncate_passes_small_tables_through():
    steps = [{"Data Retrieved": {"headers": ["x"], "rows": [[1], [2]], "summary": "s"}}]
    assert _truncate_steps_for_llm(steps) == steps


# ---- export_bqca_csv ----

def test_returns_error_when_no_payload_in_state():
    result = export_bqca_csv(_ctx({}))
    assert result["status"] == "ERROR"
    assert "尚無" in result["error_details"]


def test_returns_error_when_no_data_retrieved_step():
    ctx = _ctx({"last_bqca_payload": {"question": "q", "steps": [{"Answer": "hi"}]}})
    result = export_bqca_csv(ctx)
    assert result["status"] == "ERROR"
    assert "資料表" in result["error_details"]


def test_pending_auth_propagates_from_negotiate_creds():
    ctx = _ctx({
        "last_bqca_payload": {
            "question": "q",
            "steps": [
                {"Data Retrieved": {"headers": ["x"], "rows": [[1]], "summary": ""}}
            ],
        }
    })
    with patch(
        "app.tools._negotiate_creds",
        return_value={"pending": True, "message": "Awaiting user authentication"},
    ):
        result = export_bqca_csv(ctx)
    assert result["status"] == "PENDING_AUTH"
    assert result["pending"] is True


def test_success_uploads_csv_to_drive_and_returns_download_url():
    ctx = _ctx({
        "last_bqca_payload": {
            "question": "Top 3 VIP 玩家",
            "steps": [
                {
                    "Data Retrieved": {
                        "headers": ["player_id", "tier"],
                        "rows": [["A1", "gold"], ["B2", "silver"], ["C3", "bronze"]],
                        "summary": "Showing all 3 rows.",
                    }
                }
            ],
        }
    })

    fake_drive = MagicMock()
    fake_drive.files().create().execute.return_value = {"id": "FILE_ID_123"}
    fake_drive.permissions().create().execute.return_value = {}

    with patch("app.tools._negotiate_creds", return_value=Credentials(token="x")), \
         patch("app.tools.build", return_value=fake_drive):
        result = export_bqca_csv(ctx)

    assert result["status"] == "SUCCESS"
    assert result["url"] == "https://drive.google.com/uc?export=download&id=FILE_ID_123"
    assert result["row_count"] == 3
    assert result["filename"].startswith("bqca-Top-3-VIP")
    assert result["filename"].endswith(".csv")


def test_success_concatenates_multiple_data_retrieved_tables():
    ctx = _ctx({
        "last_bqca_payload": {
            "question": "compare",
            "steps": [
                {"Data Retrieved": {"headers": ["a"], "rows": [[1]], "summary": ""}},
                {"Data Retrieved": {"headers": ["b"], "rows": [[2], [3]], "summary": ""}},
            ],
        }
    })

    captured = {}

    def _capture(stream, mimetype):
        captured["data"] = stream.read()
        stream.seek(0)
        return MagicMock()

    fake_drive = MagicMock()
    fake_drive.files().create().execute.return_value = {"id": "ID"}
    fake_drive.permissions().create().execute.return_value = {}

    with patch("app.tools._negotiate_creds", return_value=Credentials(token="x")), \
         patch("app.tools.build", return_value=fake_drive), \
         patch("app.tools.MediaIoBaseUpload", side_effect=_capture):
        result = export_bqca_csv(ctx)

    assert result["status"] == "SUCCESS"
    assert result["row_count"] == 3
    text = captured["data"].decode("utf-8-sig")
    assert "# Table 2" in text
