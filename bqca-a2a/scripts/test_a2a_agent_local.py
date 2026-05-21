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

import asyncio
import os
import uuid
from a2a.types import (
    Message,
    MessageSendParams,
    Part,
    Role,
    TextPart,
    Task,
)
from app.agent_runtime_app import agent_runtime

async def main():
    print("==================================================")
    print("Initializing Agent Runtime locally...")
    print("==================================================")
    
    # Set mock environment variables. Project comes from env so the script
    # works against any GCP project without code changes.
    project = os.environ.get("GOOGLE_CLOUD_PROJECT") or os.environ.get(
        "BQCA_DATA_AGENT_PROJECT", ""
    )
    if not project:
        raise SystemExit(
            "Set GOOGLE_CLOUD_PROJECT (or BQCA_DATA_AGENT_PROJECT) before running."
        )
    os.environ["INTEGRATION_TEST"] = "TRUE"
    os.environ["GOOGLE_CLOUD_PROJECT"] = project
    os.environ["GOOGLE_CLOUD_LOCATION"] = "global"

    # Initialize vertexai with global location so set_up does not override it to us-central1
    import vertexai
    vertexai.init(project=project, location="global")
    agent_runtime._tmpl_attrs["location"] = "global"
    
    # Initialize the reasoning engine / A2A agent
    agent_runtime.set_up()
    
    print("\n[SUCCESS] Agent Runtime initialized successfully!")
    print("--------------------------------------------------")
    print("Agent Card Details:")
    print(f" - Name: {agent_runtime.agent_card.name}")
    print(f" - Description: {agent_runtime.agent_card.description}")
    print(f" - Skills: {[s.name for s in agent_runtime.agent_card.skills]}")
    print("--------------------------------------------------")
    
    # Construct a mock A2A message. Since it is off-topic, the agent
    # will return its standard text fallback without calling external GCP tools.
    user_msg = Message(
        message_id=f"msg-user-{uuid.uuid4()}",
        role=Role.user,
        parts=[Part(root=TextPart(text="你好，請問天空為什麼是藍色的？"))],
    )
    
    params = MessageSendParams(message=user_msg)
    
    print("\nSending off-topic A2A query to local agent handler...")
    print(f"Query: '你好，請問天空為什麼是藍色的？'")
    print("--------------------------------------------------")
    
    task_or_message = await agent_runtime.request_handler.on_message_send(params)
    
    print("\n==================================================")
    print("A2A Protocol Execution Completed!")
    print("==================================================")
    print(f"Response Type: {type(task_or_message)}")
    
    if isinstance(task_or_message, Task):
        print(f"Task Status State: {task_or_message.status.state}")
        if task_or_message.artifacts:
            print("\nTask Artifacts:")
            for artifact in task_or_message.artifacts:
                for part in artifact.parts:
                    print(f" - {part.root}")
    else:
        print("\nDirect Message Parts:")
        for part in task_or_message.parts:
            # Print the text if available
            if hasattr(part.root, "text"):
                print(f" - [Text]: {part.root.text}")
            else:
                print(f" - [Other]: {part.root}")
    print("==================================================")

if __name__ == "__main__":
    asyncio.run(main())
