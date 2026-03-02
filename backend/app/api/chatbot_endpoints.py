from fastapi import APIRouter, HTTPException, Header
from pydantic import BaseModel
from typing import Dict, Any, Optional
from app.services.chatbot import chatbot_service

router = APIRouter()

class ChatQuery(BaseModel):
    message: str
    context: Dict[str, Any]

@router.post("/query")
async def chat_with_assistant(query: ChatQuery, authorization: Optional[str] = Header(None)):
    """User query to the Credentialing AI Assistant."""
    print(f"🤖 Chatbot Query: {query.message}")
    # We could add auth checks here if needed
    try:
        response = await chatbot_service.query(query.message, query.context)
        return {"status": "success", "response": response}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
