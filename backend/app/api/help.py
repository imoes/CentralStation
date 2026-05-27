from pathlib import Path

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import CurrentUser
from app.core.database import get_db
from app.services.settings import get_llm_config

router = APIRouter(prefix="/help", tags=["help"])
_README = Path("/app/README.md")


@router.get("/content")
async def get_content(_: CurrentUser) -> dict:
    text = _README.read_text(encoding="utf-8") if _README.exists() else "# CentralStation\n\nDokumentation nicht gefunden."
    return {"content": text}


class HelpAskRequest(BaseModel):
    question: str


@router.post("/ask")
async def ask_help(
    body: HelpAskRequest,
    _: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> dict:
    llm_cfg = await get_llm_config(db)
    if not llm_cfg.base_url:
        return {"answer": "LLM nicht konfiguriert. Bitte zuerst in den KI-Einstellungen einen Endpunkt hinterlegen."}

    content = _README.read_text(encoding="utf-8") if _README.exists() else ""

    from langchain_core.messages import HumanMessage, SystemMessage
    from langchain_openai import ChatOpenAI

    llm = ChatOpenAI(
        base_url=llm_cfg.base_url,
        model=llm_cfg.model,
        api_key=llm_cfg.api_key or "none",
        temperature=0.3,
        max_tokens=900,
    )
    messages = [
        SystemMessage(content=(
            "Du bist ein Hilfe-Assistent für CentralStation, ein IT-Operations-Dashboard.\n"
            "Beantworte Fragen präzise auf Basis der Dokumentation.\n"
            "Antworte immer auf Deutsch. Wenn etwas nicht in der Doku steht, sage es ehrlich.\n\n"
            f"--- DOKUMENTATION ---\n{content}\n--- ENDE ---"
        )),
        HumanMessage(content=body.question),
    ]
    response = await llm.ainvoke(messages)
    return {"answer": response.content}
