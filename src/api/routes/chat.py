from fastapi import APIRouter, HTTPException, Request
from langchain_core.messages import HumanMessage

from src.config.settings import settings
from src.agent.workflows.graph import agentic_graph
from src.agent.memory.state import ChatRequest, ChatResponse

router = APIRouter(prefix="/api/v1", tags=["Chat"])


@router.post("/chat", response_model=ChatResponse)
async def chat_endpoint(request: ChatRequest, req: Request):
    try:
        auth_header = req.headers.get("Authorization", "")
        token = auth_header.replace("Bearer ", "").strip() if "Bearer " in auth_header else auth_header.strip()

        inputs = {"messages": [HumanMessage(content=request.message)]}

        config = {
            "configurable": {
                "thread_id": request.session_id,
                "access_token": token,
            },
            "metadata": {
                "project_name": settings.langchain_project,
                "session_id": request.session_id,
            },
            "tags": ["deepagent", "chat"],
        }

        result = await agentic_graph.ainvoke(inputs, config)
        reply = result["messages"][-1].content
        return ChatResponse(
            status="success",
            reply=reply,
            extracted_data=result.get("extracted_data", {}),
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
