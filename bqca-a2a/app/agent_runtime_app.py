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

from typing import Any, AsyncIterator
from vertexai.preview.reasoning_engines import A2aAgent
from app.a2a_config import agent_card
from app.executor import create_executor
from app.agent import ge_auth_token

class AgentCardWrapper:
    """Wrapper to make Pydantic AgentCard look like a Protobuf Message to Vertex AI SDK,
    while keeping Pydantic's model_dump/JSON serialization for a2a-sdk.
    """
    def __init__(self, pydantic_card):
        self._pydantic = pydantic_card
        
        # Convert pydantic agent_card to protobuf message
        from a2a.grpc import a2a_pb2
        from google.protobuf import json_format
        card_dict = pydantic_card.model_dump(mode="json", exclude_none=True)
        self._proto = a2a_pb2.AgentCard()
        json_format.ParseDict(card_dict, self._proto)

    def __getattr__(self, name):
        return getattr(self._proto, name)

    @property
    def _pb(self):
        return self._proto

    def model_dump(self, *args, **kwargs):
        return self._pydantic.model_dump(*args, **kwargs)

class CustomA2aAgent(A2aAgent):
    """Custom A2A Agent extending Vertex AI's A2aAgent.

    Extracts incoming bearer tokens from HTTP headers and stores them
    in a request-scoped ContextVar to allow tools to authenticate.
    """

    async def on_message_send(self, request, context) -> dict[str, Any]:
        if request and hasattr(request, "headers"):
            auth = request.headers.get("authorization", "")
            if auth and auth.lower().startswith("bearer "):
                ge_auth_token.set(auth[7:])
        return await super().on_message_send(request, context)

    async def on_message_send_stream(self, request, context) -> AsyncIterator[str]:
        if request and hasattr(request, "headers"):
            auth = request.headers.get("authorization", "")
            if auth and auth.lower().startswith("bearer "):
                ge_auth_token.set(auth[7:])
        async for chunk in super().on_message_send_stream(request, context):
            yield chunk

# Wrap the agent card for compatibility
wrapped_card = AgentCardWrapper(agent_card)

# This is the entrypoint object that agents-cli deploy looks for by default
# when deployment_target is "agent_runtime".
agent_runtime = CustomA2aAgent(
    agent_card=wrapped_card,
    agent_executor_builder=create_executor,
)

