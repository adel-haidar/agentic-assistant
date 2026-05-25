# Agentic Assistant

A personal email assistant that monitors your Microsoft Outlook inbox, triages incoming emails using an LLM, and automatically drafts responses for messages that need a reply.

## How it works

1. **Sync** — fetches new emails from your inbox and junk folder using the Microsoft Graph API delta endpoint (only new messages since the last sync are processed).
2. **Context** — for each email, the agent queries the MCP memory server (personal knowledge from past AI conversations) and searches OneDrive for documents relevant to the email subject.
3. **Assess** — each email, together with the retrieved context, is sent to an LLM (Amazon Bedrock) which decides whether it needs a personal response and categorises it (personal, work, marketing, security, notification, unknown).
4. **Draft** — for emails that need a reply, the LLM writes a draft response informed by the personal context and any relevant OneDrive documents. The draft is saved directly to your Outlook drafts folder via the Graph API.

## Stack

- **FastAPI** — HTTP API
- **Microsoft Graph API** — email access, draft creation, and OneDrive search
- **Amazon Bedrock** (Amazon Nova Lite) — email triage and response drafting
- **MCP memory server** — personal context from past AI conversations (Claude, Codex, Antigravity)
- **Pydantic Settings** — configuration via environment variables

## Setup

### 1. Register a Microsoft Entra app

Create an app registration at [portal.azure.com](https://portal.azure.com) with the `Mail.ReadWrite` and `offline_access` delegated permissions and set the redirect URI to `http://localhost:8000/auth/microsoft/callback`.

### 2. Configure environment variables

Create a `.env` file or export the following variables:

```
MS_CLIENT_ID=<your-app-client-id>
MS_CLIENT_SECRET=<your-app-client-secret>
MS_REDIRECT_URI=http://localhost:8000/auth/microsoft/callback
MS_TENANT=consumers
AWS_REGION=eu-central-1
BEDROCK_MODEL_ID=eu.amazon.nova-2-lite-v1:0
USER_NAME=Adel
MCP_MEMORY_URL=http://<ec2-ip>:<port>/sse   # optional — omit to disable memory lookups
```

### 3. Configure AWS credentials

Make sure your environment has valid AWS credentials with permission to invoke the Bedrock model (e.g. via `aws configure` or an IAM role).

### 4. Install dependencies and run

```bash
uv sync
uv run fastapi dev main.py
```

## API

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | Health check |
| `GET` | `/auth/microsoft/login` | Redirect to Microsoft OAuth login |
| `GET` | `/auth/microsoft/callback` | OAuth callback — exchanges code for tokens |
| `GET` | `/email/sync` | Fetch new emails, triage, and save drafts |

### Authentication flow

Visit `/auth/microsoft/login` in your browser. After granting consent you will be redirected back and the app stores the refresh token in memory. All subsequent `/email/sync` calls use this token to obtain fresh access tokens automatically.

> **Note:** The token store is in-memory only and is lost on restart. For production use, persist the refresh token to a database.
