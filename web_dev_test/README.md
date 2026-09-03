# DeepAgent — Dev Test UI

Streamlit interface to test the DeepAgent chat API locally.

## Setup

### 1. Start the FastAPI backend

```bash
cd /path/to/DeepAgent
source venv/bin/activate
uvicorn src.main:app --reload
```

Server runs at `http://localhost:8000`. API docs: `http://localhost:8000/docs`

### 2. Start Streamlit

```bash
cd web_dev_test
pip install -r requirements.txt
streamlit run streamlit_app.py
```

Opens at `http://localhost:8501`.

### 3. Configure

In the sidebar:
- **JWT Token** — paste your Bearer token from DeepPro auth
- **Tenant ID** — defaults to `deeppro`
- **User ID** — defaults to `dev_user`
- **Clear chat** — wipes conversation history

## API Contract

```
POST http://localhost:8000/api/v1/chat

Headers:
  Authorization: Bearer <token>
  Content-Type: application/json

Body:
  {
    "tenant_id": "deeppro",
    "user_id": "dev_user",
    "message": "Your question here"
  }

Response:
  {
    "status": "success",
    "reply": "Agent's response",
    "extracted_data": {}
  }
```

## Dependencies

- `streamlit>=1.40.0`
- `requests>=2.32.0`
