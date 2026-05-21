# bqca-adk-demo

Two ADK agents that wrap a BigQuery Conversational Analytics (BQCA) data agent and turn its responses into rendered chat UI plus Google Slides decks. Designed to be invoked from **Gemini Enterprise**, which handles user OAuth and injects the resulting token into the agent at runtime.

| Project | Deploy target | Protocol |
|---|---|---|
| [`bqca/`](./bqca) | Vertex AI **Agent Runtime** | ADK |
| [`bqca-a2a/`](./bqca-a2a) | **Cloud Run** | A2A (FastAPI + agent card) |

Both projects share the same agent logic — the difference is the deployment surface and how Gemini Enterprise invokes them.

---

## Prerequisites

| Tool | Install |
|---|---|
| `uv` | https://docs.astral.sh/uv/getting-started/installation |
| `agents-cli` | `uv tool install google-agents-cli` |
| `gcloud` | https://cloud.google.com/sdk/docs/install |
| `gh` (optional) | https://cli.github.com |

You also need:

- A Google Cloud project with billing enabled (this guide calls it `<DEPLOY_PROJECT>`)
- A Gemini Enterprise app in another (or the same) project (`<GE_PROJECT>`) — created in [Cloud Console → Gemini Enterprise → Apps](https://console.cloud.google.com/gemini-enterprise/apps)
- A pre-built BigQuery Conversational Analytics **data agent** — you'll need its project, location, and agent ID
- An OAuth 2.0 client (type **Web application**) — created in [Cloud Console → APIs & Services → Credentials](https://console.cloud.google.com/apis/credentials). Add `https://vertexaisearch.cloud.google.com/oauth-redirect` to **Authorized redirect URIs**.
- An [OAuth consent screen](https://console.cloud.google.com/apis/credentials/consent) configured for that client. **While the consent screen is in Testing publishing status, every user who consents must be added under Test users** — otherwise the first invocation in Gemini Enterprise fails with "Access blocked: this app's request is invalid."

Enable required APIs in `<DEPLOY_PROJECT>`:

```bash
gcloud services enable \
  aiplatform.googleapis.com \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  secretmanager.googleapis.com \
  geminidataanalytics.googleapis.com \
  discoveryengine.googleapis.com \
  --project=<DEPLOY_PROJECT>
```

Authenticate locally and set a default region (anything Vertex AI + Cloud Run supports — e.g. `us-central1`, `us-east1`, `europe-west1`):

```bash
gcloud auth login
gcloud auth application-default login
gcloud config set project <DEPLOY_PROJECT>
gcloud config set ai/region <REGION>
gcloud config set run/region <REGION>
```

Look up the two project numbers (used in IAM bindings and Gemini Enterprise authorization resource names below):

```bash
gcloud projects describe <DEPLOY_PROJECT> --format="value(projectNumber)"   # → <PROJECT_NUMBER>
gcloud projects describe <GE_PROJECT>     --format="value(projectNumber)"   # → <GE_PROJECT_NUMBER>
```

---

## 1. Configure `.env`

Each subproject reads runtime config from its own `.env` (gitignored). Create them:

```bash
# bqca/.env
cat > bqca/.env <<EOF
BQCA_DATA_AGENT_PROJECT=<DATA_AGENT_PROJECT>
BQCA_DATA_AGENT_LOCATION=global
BQCA_DATA_AGENT_ID=<DATA_AGENT_ID>

AUTH_ID=bqca-bigquery-auth
GE_PROJECT_ID=<GE_PROJECT>

OAUTH_CLIENT_ID=<OAUTH_CLIENT_ID>
OAUTH_CLIENT_SECRET=<OAUTH_CLIENT_SECRET>
OAUTH_SCOPES="https://www.googleapis.com/auth/bigquery https://www.googleapis.com/auth/presentations https://www.googleapis.com/auth/drive.file"

USE_ADC=true  # local dev only — see note below
EOF
```

```bash
# bqca-a2a/.env  (same as above, except AUTH_ID)
cp bqca/.env bqca-a2a/.env
sed -i 's/AUTH_ID=bqca-bigquery-auth/AUTH_ID=bqca-bigquery-auth-a2a/' bqca-a2a/.env
```

**`USE_ADC=true` is for local dev only.** It tells the agent to use your ADC credentials instead of going through OAuth. Production deployments inject the OAuth token from Gemini Enterprise — leave `USE_ADC` unset in deployed env vars.

---

## 2. Local sanity check (optional)

```bash
cd bqca
uv sync
agents-cli playground   # opens local web UI
```

Ask "VIP 流失人數?" — should reach the BQCA data agent via ADC.

---

## 3. Deploy

The two projects deploy to different targets and can run in parallel.

### 3a. `bqca` → Agent Runtime

```bash
cd bqca

# Put the OAuth client secret in Secret Manager (one-time)
printf '%s' '<OAUTH_CLIENT_SECRET>' | gcloud secrets create oauth-client-secret \
  --project=<DEPLOY_PROJECT> --replication-policy=automatic --data-file=-

# Grant the Agent Runtime service agent access to the secret
gcloud secrets add-iam-policy-binding oauth-client-secret \
  --project=<DEPLOY_PROJECT> \
  --member="serviceAccount:service-<PROJECT_NUMBER>@gcp-sa-aiplatform-re.iam.gserviceaccount.com" \
  --role=roles/secretmanager.secretAccessor

# Deploy (5-10 minutes). --update-env-vars is one comma-separated string in
# double quotes; the spaces inside OAUTH_SCOPES are preserved because gcloud
# only splits on the commas between KEY=VALUE pairs.
agents-cli deploy --no-confirm-project --region <REGION> \
  --update-env-vars "BQCA_DATA_AGENT_PROJECT=<DATA_AGENT_PROJECT>,BQCA_DATA_AGENT_LOCATION=global,BQCA_DATA_AGENT_ID=<DATA_AGENT_ID>,AUTH_ID=bqca-bigquery-auth,GE_PROJECT_ID=<GE_PROJECT>,OAUTH_CLIENT_ID=<OAUTH_CLIENT_ID>,OAUTH_SCOPES=https://www.googleapis.com/auth/bigquery https://www.googleapis.com/auth/presentations https://www.googleapis.com/auth/drive.file" \
  --secrets "OAUTH_CLIENT_SECRET=oauth-client-secret"
```

The deploy writes `bqca/deployment_metadata.json` containing the reasoning engine resource name. Save it — you'll need it for registration.

### 3b. `bqca-a2a` → Cloud Run

```bash
cd bqca-a2a

# Grant Secret Manager access to the Cloud Run runtime SA (default compute SA shown)
gcloud secrets add-iam-policy-binding oauth-client-secret \
  --project=<DEPLOY_PROJECT> \
  --member="serviceAccount:<PROJECT_NUMBER>-compute@developer.gserviceaccount.com" \
  --role=roles/secretmanager.secretAccessor

# Deploy
agents-cli deploy --no-confirm-project --region <REGION>

# Push runtime env vars + secret binding
gcloud run services update bqca-agent-a2a \
  --region=<REGION> --project=<DEPLOY_PROJECT> \
  --update-env-vars="BQCA_DATA_AGENT_PROJECT=<DATA_AGENT_PROJECT>,BQCA_DATA_AGENT_LOCATION=global,BQCA_DATA_AGENT_ID=<DATA_AGENT_ID>,AUTH_ID=bqca-bigquery-auth-a2a,GE_PROJECT_ID=<GE_PROJECT>,OAUTH_CLIENT_ID=<OAUTH_CLIENT_ID>,OAUTH_SCOPES=https://www.googleapis.com/auth/bigquery https://www.googleapis.com/auth/presentations https://www.googleapis.com/auth/drive.file" \
  --update-secrets=OAUTH_CLIENT_SECRET=oauth-client-secret:latest
```

Note the **Service URL** in the deploy output — `https://bqca-agent-a2a-<HASH>.<REGION>.run.app`.

Allow Gemini Enterprise to invoke the Cloud Run service:

```bash
gcloud run services add-iam-policy-binding bqca-agent-a2a \
  --region=<REGION> --project=<DEPLOY_PROJECT> \
  --member="serviceAccount:service-<GE_PROJECT_NUMBER>@gcp-sa-discoveryengine.iam.gserviceaccount.com" \
  --role=roles/run.invoker
```

---

## 4. Register to Gemini Enterprise

Two-step: create an **Authorization** resource per agent, then **publish** each agent referencing it. The authorization ID must match the agent's `AUTH_ID` env var so Gemini Enterprise injects the token under the key the agent reads from `tool_context.state`.

### 4a. Create the Authorization resources

```bash
GE_PROJECT=<GE_PROJECT>
CLIENT_ID=<OAUTH_CLIENT_ID>
CLIENT_SECRET=<OAUTH_CLIENT_SECRET>
SCOPES="https://www.googleapis.com/auth/bigquery https://www.googleapis.com/auth/presentations https://www.googleapis.com/auth/drive.file"
SCOPES_ENC=$(python3 -c "import urllib.parse;print(urllib.parse.quote('$SCOPES'))")
AUTH_URI="https://accounts.google.com/o/oauth2/v2/auth?client_id=${CLIENT_ID}&redirect_uri=https://vertexaisearch.cloud.google.com/oauth-redirect&scope=${SCOPES_ENC}&include_granted_scopes=true&response_type=code&access_type=offline&prompt=consent"
TOKEN=$(gcloud auth print-access-token)

for AUTH_ID in bqca-bigquery-auth bqca-bigquery-auth-a2a; do
  curl -sS -X POST \
    "https://discoveryengine.googleapis.com/v1alpha/projects/${GE_PROJECT}/locations/global/authorizations?authorizationId=${AUTH_ID}" \
    -H "Authorization: Bearer ${TOKEN}" \
    -H "X-Goog-User-Project: ${GE_PROJECT}" \
    -H "Content-Type: application/json" \
    -d "{
      \"name\": \"projects/${GE_PROJECT}/locations/global/authorizations/${AUTH_ID}\",
      \"serverSideOauth2\": {
        \"clientId\": \"${CLIENT_ID}\",
        \"clientSecret\": \"${CLIENT_SECRET}\",
        \"authorizationUri\": \"${AUTH_URI}\",
        \"tokenUri\": \"https://oauth2.googleapis.com/token\"
      }
    }"
done
```

A reference Python implementation lives at [`bqca/tools/register_oauth.py`](./bqca/tools/register_oauth.py) — it reads `AUTH_ID` and `OAUTH_*` from the local `.env` and POSTs the same authorization resource. Run it once per agent, setting `AUTH_ID` to the value expected by that agent (`bqca-bigquery-auth` for `bqca`, `bqca-bigquery-auth-a2a` for `bqca-a2a`).

### 4b. Publish each agent

Look up your `<GE_APP_RESOURCE>` (full resource name including project number):

```bash
list_ge_apps() {
  curl -sS -H "Authorization: Bearer $(gcloud auth print-access-token)" \
    -H "X-Goog-User-Project: <GE_PROJECT>" \
    "https://discoveryengine.googleapis.com/v1/projects/<GE_PROJECT>/locations/global/collections/default_collection/engines" \
    | python3 -c "import json,sys;[print(e['name'],e['displayName']) for e in json.load(sys.stdin)['engines']]"
}
list_ge_apps
```

Take the `name` of the app you want to register against and use it as `--gemini-enterprise-app-id`.

**`bqca` (ADK on Agent Runtime):**

```bash
cd bqca
agents-cli publish gemini-enterprise \
  --gemini-enterprise-app-id "<GE_APP_RESOURCE>" \
  --display-name "ApexZenith Games BQCA" \
  --description "BigQuery Conversational Analytics with Google Slides reporting." \
  --authorization-id "projects/<GE_PROJECT_NUMBER>/locations/global/authorizations/bqca-bigquery-auth"
```

The reasoning engine ID is auto-detected from `deployment_metadata.json`.

**`bqca-a2a` (A2A on Cloud Run):**

```bash
cd bqca-a2a
agents-cli publish gemini-enterprise \
  --registration-type a2a \
  --deployment-target cloud_run \
  --agent-card-url "https://bqca-agent-a2a-<HASH>.<REGION>.run.app/a2a/app/.well-known/agent-card.json" \
  --gemini-enterprise-app-id "<GE_APP_RESOURCE>" \
  --display-name "ApexZenith Games BQCA (A2A)" \
  --description "A2A variant served from Cloud Run." \
  --authorization-id "projects/<GE_PROJECT_NUMBER>/locations/global/authorizations/bqca-bigquery-auth-a2a"
```

---

## 5. Use it

Open your Gemini Enterprise app, start a new conversation, and pick one of the registered agents. First invocation triggers a Google OAuth consent screen (the scopes from `OAUTH_SCOPES`). After consent, ask a VIP analytics question — the agent calls BQCA, renders SQL + tables + charts inline, and offers buttons to export CSV or generate a Slides deck.

---

## Architecture notes

**OAuth flow.** Gemini Enterprise holds the user's refresh token under the `authorizationId` you attached to the agent. Each invocation, it injects a fresh access token into `tool_context.state["<AUTH_ID>"]` (sometimes under `temp:<AUTH_ID>`) — see the [`adk-ae-oauth` sample](https://github.com/google/adk-samples/tree/main/python/agents/adk-ae-oauth#production-agent-runtime--gemini-enterprise) for the canonical three-stage `negotiate_creds()` pattern this implementation is based on. The agent's `_negotiate_creds` ([`bqca/app/tools.py`](./bqca/app/tools.py)) checks both keys, then falls back to the ADK auth-request flow for local dev.

**Why two variants.** ADK on Agent Runtime is the lower-friction managed path (no Dockerfile, single deploy command). A2A on Cloud Run is the same agent surfaced via the Agent-to-Agent protocol — useful when you want a callable HTTP endpoint with an agent card, or to compose with other A2A agents.

**Surface IDs.** The agent emits A2UI v0.8 envelopes (`bqca_slides`, `bqca_csv`, `bqca_auth`, `bqca_error`, `data_agent`) so the Gemini Enterprise frontend can render structured panels alongside chat text.

---

## 6. Cleanup

Agent Runtime has no `gcloud` CLI, so deletion goes through the REST API. Everything else uses normal `gcloud`.

```bash
# Delete Agent Runtime deployment (REST)
curl -X DELETE -H "Authorization: Bearer $(gcloud auth print-access-token)" \
  "https://<REGION>-aiplatform.googleapis.com/v1/projects/<DEPLOY_PROJECT>/locations/<REGION>/reasoningEngines/<REASONING_ENGINE_ID>"

# Delete Cloud Run service
gcloud run services delete bqca-agent-a2a --region=<REGION> --project=<DEPLOY_PROJECT>

# Delete a Gemini Enterprise agent registration (REST) — repeat per agent
curl -X DELETE -H "Authorization: Bearer $(gcloud auth print-access-token)" \
  -H "X-Goog-User-Project: <GE_PROJECT>" \
  "https://discoveryengine.googleapis.com/v1alpha/<GE_APP_RESOURCE>/assistants/default_assistant/agents/<AGENT_ID>"

# Delete a Gemini Enterprise authorization (REST) — repeat per auth ID
curl -X DELETE -H "Authorization: Bearer $(gcloud auth print-access-token)" \
  -H "X-Goog-User-Project: <GE_PROJECT>" \
  "https://discoveryengine.googleapis.com/v1alpha/projects/<GE_PROJECT>/locations/global/authorizations/bqca-bigquery-auth"

# Delete Secret Manager secret
gcloud secrets delete oauth-client-secret --project=<DEPLOY_PROJECT>
```

---

## Reference

- ADK Agent Runtime: https://adk.dev/deploy/agent-runtime
- Cloud Run deployment: https://adk.dev/deploy/cloud-run
- A2A protocol: https://google.github.io/adk-docs/a2a/
- BigQuery Conversational Analytics: https://cloud.google.com/bigquery/docs/conversational-analytics
- Gemini Enterprise authorizations: https://cloud.google.com/generative-ai-app-builder/docs/reference/rest/v1alpha/projects.locations.authorizations
