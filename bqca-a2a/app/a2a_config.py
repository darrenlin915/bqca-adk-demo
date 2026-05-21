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

from a2a.types import AgentSkill
from vertexai.preview.reasoning_engines.templates.a2a import create_agent_card

# Define the skills/capabilities of this agent
bqca_skill = AgentSkill(
    id="bqca_analytics",
    name="ApexZenith Games & BQCA Analytics",
    description="Analyze BigQuery datasets, generate SQL queries, render data charts, and compose Google Slides reports.",
    tags=["bigquery", "google-slides", "data-analysis", "presentation"],
    examples=[
        "Analyze the sales data and generate a Google Slides summary",
        "Create a slide deck showing the monthly active users trend",
    ],
    input_modes=["text/plain"],
    output_modes=["text/plain"],
)

# Build the AgentCard
agent_card = create_agent_card(
    agent_name="ApexZenith Games Analytics Agent",
    description="An AI agent specializing in BigQuery data analytics and direct generation of beautiful Google Slides presentations.",
    skills=[bqca_skill],
)
