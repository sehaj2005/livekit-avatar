# Realtime AI Avatar — MVP starter

Implements the proposal's first milestones: FastAPI room/token service, React meeting shell, LiveKit voice agent, optional Simli avatar. **Not production ready**: the demo API has no user authentication, authorization, persisted meetings or rate limits; do not expose to the public internet.

## Prerequisites
- Python 3.11+ recommended, Node.js 20+, and a LiveKit Cloud project.
- Simli API key and face ID when you enable the video avatar.
- Agent needs working cloud dispatch registration; local dev should run a worker that is registered under `avatar-agent`. Check the LiveKit CLI documentation if your dev deployment is isolated.

## 1. Configure
Copy `.env.example` to `.env` and fill the keys. Leave `ENABLE_SIMLI=false` for voice-only Phase B.
Set `GOOGLE_API_KEY` to use direct Gemini transcription, replies, and speech synthesis.
Set `MATEO_GEMINI_LIVE=true` to use Gemini Live for meeting speech. Simli can
stay enabled, and uploaded-document answers still use the local meeting lookup.
Set it to `false` to use the speech-to-text, reply model, and text-to-speech pipeline.
On Windows, Mateo can use the installed Windows voice if Gemini speech synthesis fails.
If the key is empty, meeting voice uses LiveKit Inference. `GEMINI_API_KEY`
configures the separate Gemini Skin Check route.
To generate Mateo's meeting replies locally, set `MATEO_OLLAMA_MODEL` to an
installed model listed by `ollama list`. Keep Ollama running on port 11434.
Mateo preloads the model when a meeting starts and uses the configured cloud
reply model if Ollama fails before producing a response.
On Windows with Gemini meeting voice enabled, set `MATEO_USE_WINDOWS_VOICE=true`
to speak with the installed Windows voice and fall back to Gemini if it fails.

## 2. Start backend (Windows PowerShell)
```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements-backend.txt
uvicorn backend.main:app --reload --port 8000
```

## 3. Start agent (a second PowerShell terminal)
```powershell
.\.venv\Scripts\Activate.ps1
pip install -r requirements-agent.txt
python agent/agent.py dev
```

For modern LiveKit CLI workflows, `lk agent dev` is also supported when the CLI is configured. Explicit dispatch may target a deployment; if your dev worker is scoped to a nonproduction deployment, adjust dispatch configuration to match it.

## 4. Start frontend (third PowerShell terminal)
```powershell
cd frontend
npm install
npm run dev
```
Open http://localhost:5173. Browser permission prompts for camera/mic require allowing access.

## 5. Enable Simli (after voice works)
- Select a Simli face in your account and set `SIMLI_FACE_ID` and `SIMLI_API_KEY` in `.env`.
- Set `ENABLE_SIMLI=true` and restart the agent worker.
- Confirm the avatar's video and **one** audio path are visible/audible in the room.
- Use the LiveKit docs to tune the latest avatar/audio output configuration; no credentials belong in frontend code.

## Verify
- GET http://localhost:8000/health returns `{"status":"ok"}`.
- POST `/api/v1/meetings` returns new room metadata.
- POST `/api/v1/meetings/{meeting_id}/join` returns a short-lived participant JWT.
- Start worker and request agent start: confirm agent joins, speaks and responds to interruptions.

## Share meeting invites

To share invites from a local development session, build the frontend and run
the guest service from the project root.

```powershell
npm --prefix frontend run build
.\.venv\Scripts\python.exe -m uvicorn backend.guest_app:app --host 127.0.0.1 --port 8010 --no-access-log
```

Point an HTTPS host or tunnel at port 8010. Set `VITE_GUEST_JOIN_URL` in
`frontend/.env.local` to the HTTPS guest address ending in `/join`, then
restart Vite. Copy the invite again from the meeting. The guest service serves
the join page and participant APIs. The host and admin UI use the local app.

## Skin Check access

Meeting participants do not enter development tokens. The frontend automatically
sends their short-lived LiveKit participant token to `/api/v1/skin/local-rag`.
The backend verifies the token, meeting, registered participant and live room
connection, and requires consent to capture and process the snapshot.

The shared medicine document upload API at `/api/v1/medicine/upload` requires
an active admin session. Uploaded material remains unreviewed.

Participants can use **Upload RAG Documents** in Skin Check for TXT, MD,
or text-based PDF files (10 MB and 100,000 extracted characters per file).
These files are indexed separately by meeting and participant, with up to 5
files per participant and 20 per meeting. Access expires after 24 hours; expired
rows are purged on subsequent uploads. Files are not added to the shared admin
corpus. Rejoining under a new participant identity does not grant access to the
previous identity's files.

Skin explanations quote matching passages from the participant's files and name
their sources. To ask Mateo about an upload, say something like “What does my
uploaded document say about redness?” General questions go directly to Mateo's
conversation model. Document answers stay out of the shared chat history, while
spoken replies are audible to the meeting.

Default analysis limits are 3 requests per participant per 10 minutes, 20 per
meeting per hour, and 1 concurrent analysis across workers sharing the analytics
SQLite database. Configure `SKIN_PARTICIPANT_LIMIT_10M`, `SKIN_MEETING_LIMIT_1H`
and `SKIN_CONCURRENT_LIMIT` on the backend. Rejected busy requests do not use
quota; admitted failures do. A browser disconnect does not free capacity while
the model is still running. Reservations from crashed workers expire after at
least 15 minutes. Request metadata expires after a day; images are not persisted
by the quota system. Independently hosted replicas need a shared quota store.

Development search/preview endpoints remain separately gated by their existing
development flags and token; they are not used by the participant flow.
For deployment, configure `VITE_API_URL` for the frontend and `FRONTEND_ORIGIN`
as a comma-separated list of allowed frontend origins on the backend. Provider
keys and signing secrets belong only on the backend. This change uses meeting
invitations for participant access; it does not introduce user account login or
remove the other production work described below.

## Deferred endpoints & work
The proposal's agent stop, meeting end, transcript logging, telemetry, identity/permissions, persistent storage, and production rate limiting are deliberately excluded from this starter. The agent start endpoint is only a local demo dispatch. In production add authentication and room-owner authorization to every endpoint, ensure secure room lifecycle, avoid duplicate dispatch under concurrency, and revoke participant access properly.

References: https://docs.livekit.io/agents/start/voice-ai/ , https://docs.livekit.io/agents/models/avatar/plugins/simli/ , https://docs.livekit.io/agents/server/agent-dispatch/
