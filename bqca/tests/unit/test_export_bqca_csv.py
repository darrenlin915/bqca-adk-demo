# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0

"""Tests for the export_bqca_csv tool in app/tools.py."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from google.oauth2.credentials import Credentials

from app.tools import export_bqca_csv


def _ctx(state: dict | None = None) -> SimpleNamespace:
    """Minimal ToolContext stand-in: only `.state` is touched by the tool."""
    return SimpleNamespace(state=state if state is not None else {})


def test_returns_error_when_no_payload_in_state():
    result = export_bqca_csv(_ctx({}))
    assert result["status"] == "ERROR"
    assert "尚無" in result["error_details"]


def test_returns_error_when_no_data_retrieved_step():
    ctx = _ctx({"last_bqca_payload": {"question": "q", "steps": [{"Answer": "hi"}]}})
    result = export_bqca_csv(ctx)
    assert result["status"] == "ERROR"
    assert "資料表" in result["error_details"]


def test_returns_error_when_data_retrieved_rows_empty():
    ctx = _ctx({
        "last_bqca_payload": {
            "question": "q",
            "steps": [
                {"Data Retrieved": {"headers": ["x"], "rows": [], "summary": ""}}
            ],
        }
    })
    result = export_bqca_csv(ctx)
    assert result["status"] == "ERROR"


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
         patch("app.tools.build", return_value=fake_drive) as fake_build:
        result = export_bqca_csv(ctx)

    assert result["status"] == "SUCCESS"
    assert result["url"] == "https://drive.google.com/uc?export=download&id=FILE_ID_123"
    assert result["row_count"] == 3
    assert result["filename"].startswith("bqca-Top-3-VIP")
    assert result["filename"].endswith(".csv")
    fake_build.assert_called_once_with(
        "drive", "v3", credentials=fake_build.call_args.kwargs["credentials"],
        cache_discovery=False,
    )


def test_success_concatenates_multiple_data_retrieved_tables():
    """Multi-table payloads should produce a single CSV with `# Table N` separators."""
    ctx = _ctx({
        "last_bqca_payload": {
            "question": "compare",
            "steps": [
                {"Data Retrieved": {"headers": ["a"], "rows": [[1]], "summary": ""}},
                {"Data Retrieved": {"headers": ["b"], "rows": [[2], [3]], "summary": ""}},
            ],
        }
    })

    captured_bytes = {}

    def _capture_media_upload(stream, mimetype):
        captured_bytes["data"] = stream.read()
        stream.seek(0)
        return MagicMock()

    fake_drive = MagicMock()
    fake_drive.files().create().execute.return_value = {"id": "ID"}
    fake_drive.permissions().create().execute.return_value = {}

    with patch("app.tools._negotiate_creds", return_value=Credentials(token="x")), \
         patch("app.tools.build", return_value=fake_drive), \
         patch("app.tools.MediaIoBaseUpload", side_effect=_capture_media_upload):
        result = export_bqca_csv(ctx)

    assert result["status"] == "SUCCESS"
    assert result["row_count"] == 3
    text = captured_bytes["data"].decode("utf-8-sig")
    assert "# Table 2" in text
    assert "a\r\n1" in text or "a\n1" in text
    assert "b\r\n2" in text or "b\n2" in text
