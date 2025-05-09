# Salon AI Assistant — Quick Start & Design Notes
A two-part demo that shows how a **LiveKit voice/chat agent** can escalate unknown questions to a **Flask + Firebase supervisor dashboard** and build up a self-service knowledge-base on the fly.

---

## 1 . What’s inside

| Path | Purpose |
|------|---------|
| `supervisor_ui.py` | Flask admin dashboard for supervisors (resolve requests, edit KB). |
| `ai_agent.py` | LiveKit agent / interactive CLI client (answers callers, files help-requests). |
| `templates/index.html` | Tiny Jinja2 page listing *pending* / *resolved* tickets and KB entries. |
| `service.json` | **Firebase** service-account key *(not committed – you provide it)*. |

---

## 2 . Prerequisites

* **Python 3.10+** (virtual-env recommended)  
* **Firebase Firestore** project + service-account JSON  
* **LiveKit** cloud (or self-host) room URL + API key/secret  
* *Optional:* `ngrok` or Cloud-flared tunnel if you need to expose Flask publicly

---

## 3 . Environment variables

Create a `.env` file (or export in shell):

```bash
LIVEKIT_URL=wss://<your-cloud>.livekit.cloud
API_KEY=<livekit_api_key>
API_SECRET=<livekit_api_secret>
ROOM_NAME=salon
IDENTITY=salon-agent
```

## 4 . Setup details

```bash
# clone the project and enter the directory
git clone https://github.com/your-org/salon-ai-assistant.git
cd salon-ai-assistant

# create and activate a virtual environment
python -m venv .venv
source .venv/bin/activate         # Windows: .venv\Scripts\activate

# install all Python dependencies
pip install -r requirements.txt
```
