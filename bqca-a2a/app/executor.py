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

import os
import google.auth
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService, VertexAiSessionService
from google.adk.artifacts import GcsArtifactService, InMemoryArtifactService
from google.adk.a2a.executor.a2a_agent_executor import A2aAgentExecutor
from app.agent import app as adk_app

def create_executor(**kwargs) -> A2aAgentExecutor:
    """Builds and returns the A2aAgentExecutor with sensed services.

    This function is called by Vertex AI Reasoning Engine during setup.
    It automatically configures persistent Cloud services if running in
    the cloud, or fallbacks to in-memory mocks for local testing.
    """
    # 1. Sense environment and configure ArtifactService
    logs_bucket_name = os.environ.get("LOGS_BUCKET_NAME")
    if logs_bucket_name:
        artifact_service = GcsArtifactService(bucket_name=logs_bucket_name)
    else:
        artifact_service = InMemoryArtifactService()

    # 2. Sense environment and configure SessionService
    agent_engine_id = os.environ.get("GOOGLE_CLOUD_AGENT_ENGINE_ID")
    if agent_engine_id:
        # In cloud runtime (Reasoning Engine / Agent Engine)
        # Fetch project and location from auth default or environment
        try:
            _, project = google.auth.default()
        except Exception:
            project = os.environ.get("GOOGLE_CLOUD_PROJECT")
        
        location = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")
        session_service = VertexAiSessionService(
            project=project,
            location=location,
            agent_engine_id=agent_engine_id
        )
    else:
        session_service = InMemorySessionService()

    # 3. Build the runner
    runner = Runner(
        app=adk_app,
        artifact_service=artifact_service,
        session_service=session_service,
    )

    # 4. Instantiate A2aAgentExecutor
    return A2aAgentExecutor(runner=runner)
