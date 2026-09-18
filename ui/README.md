# 🤖 DeepAgent - Run

## Terminal 1 - Backend (FastAPI API only - port 8001)
```bash
cd /Users/dangnguyen/Documents/DeepAgent
source venv/bin/activate
uvicorn src.main:app --host 0.0.0.0 --port 8001 --reload
```
- API docs: http://localhost:8001/docs
- OpenAPI: http://localhost:8001/openapi.json

## Terminal 2 - Streamlit UI (port 8501)
```bash
cd /Users/dangnguyen/Documents/DeepAgent
source venv/bin/activate
streamlit run ui/streamlit_app.py --server.port 8501
```
- Streamlit UI: http://localhost:8501

## Test account
- Email: `testcustomer@gmail.com`
- Password: `testPassword@2003`
