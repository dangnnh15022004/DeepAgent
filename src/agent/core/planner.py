from typing import Literal
from pydantic import BaseModel, Field
from langchain_core.messages import SystemMessage, HumanMessage

from src.agent.memory.state import DeepAIState
from src.llm.providers.azure import llm
from src.agent.prompts.system_prompts import PLANNER_PROMPT

class PlanOutput(BaseModel):
    standalone_query: str = Field(
        description="Câu hỏi gốc của người dùng được viết lại cho rõ nghĩa, độc lập, dựa trên lịch sử hội thoại."
    )
    pending_tasks: list[Literal["DeepsaleopsAgent", "SysAgent", "DeeptraceAgent"]] = Field(
        description="Danh sách các tác vụ cần gọi. Nếu đã trả lời rồi hoặc không cần thiết, trả về mảng rỗng []"
    )

def planner_node(state: DeepAIState):
    planner_llm = llm.with_structured_output(PlanOutput)
    last_user_msg = state["messages"][-1].content
    
    # Lọc lịch sử sạch
    messages = state.get("messages", [])
    clean_history = [
        m for m in messages[:-1] 
        if m.type in ["human", "ai"] and not getattr(m, "tool_calls", None)
    ]
    history_text = "\n".join([f"{m.type}: {m.content}" for m in clean_history[-3:]]) if clean_history else "Không có lịch sử."
    
    final_prompt = PLANNER_PROMPT.format(history_str=history_text)
    
    messages_for_llm = [
        SystemMessage(content=final_prompt), 
        HumanMessage(content=f"Câu hỏi của khách: {last_user_msg}")
    ]
    
    result = planner_llm.invoke(messages_for_llm)
    print(f"[Planner Combo] Query: '{result.standalone_query}' -> Tasks: {result.pending_tasks}")
    
    return {
        "standalone_query": result.standalone_query, 
        "pending_tasks": result.pending_tasks
    }