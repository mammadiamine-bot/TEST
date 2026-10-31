"""Chat with local Qwen: hosts/models, conversations (private to their owner), streamed answers (Server-Sent Events)."""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from .chat import Chat
from .security import require_token
from .service import CoreError


class NewConv(BaseModel):
    host: str = "main"
    model: str = Field(min_length=1, max_length=80)
    title: str = Field(default="", max_length=80)
    worker_id: str | None = None


class Msg(BaseModel):
    content: str = Field(min_length=1, max_length=6000)
    model: str | None = Field(default=None, max_length=80)
    lang: str = Field(default="en", max_length=2)


def build_router(core, chat: Chat, billing, aitech) -> APIRouter:
    r = APIRouter(dependencies=[Depends(require_token)])
    db = core.db

    def gate(oid: str) -> None:
        if not db.one("SELECT 1 AS x FROM orgs WHERE id=?", (oid,)):
            raise HTTPException(404, "unknown organization")
        if not aitech.settings(oid)["ai_enabled"]:
            raise HTTPException(403, "the AI assistant is switched off for this client")
        billing.entitle(oid, "ai")

    @r.get("/orgs/{oid}/chat/hosts")
    def hosts(oid: str):
        gate(oid); return chat.hosts(oid)

    @r.post("/orgs/{oid}/chat/conversations", status_code=201)
    def create(oid: str, b: NewConv, request: Request):
        gate(oid); return chat.create(oid, request.state.principal.id, b.host, b.model, b.title, b.worker_id)

    @r.get("/orgs/{oid}/chat/conversations")
    def conversations(oid: str, request: Request):
        return db.all("SELECT id,title,host,model,worker_id,updated_at FROM chat_conversations WHERE org_id=? AND user_id=? ORDER BY updated_at DESC LIMIT 100", (oid, request.state.principal.id))

    @r.get("/orgs/{oid}/chat/conversations/{cid}")
    def messages(oid: str, cid: str, request: Request):
        p = request.state.principal.id
        return {"conversation": chat.conv(oid, p, cid), "messages": chat.messages(oid, p, cid)}

    @r.delete("/orgs/{oid}/chat/conversations/{cid}")
    def delete(oid: str, cid: str, request: Request):
        chat.conv(oid, request.state.principal.id, cid)
        db.execute("DELETE FROM chat_messages WHERE conv_id=?", (cid,)); db.execute("DELETE FROM chat_conversations WHERE id=?", (cid,))
        return {"ok": True}

    @r.post("/orgs/{oid}/chat/conversations/{cid}/messages")
    def send(oid: str, cid: str, b: Msg, request: Request):
        gate(oid)
        chat.on_tokens = lambda org, n: n and billing.add_ai_tokens(org, n)
        try:
            gen = chat.send(oid, request.state.principal.id, cid, b.content, b.model, b.lang)
        except CoreError as e:
            raise HTTPException(e.status, str(e))
        db.audit(request.state.principal.id, "chat.message", None, org=oid, conversation=cid, chars=len(b.content))   # never the text itself

        def sse():
            try:
                for ev in gen:
                    yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
            except CoreError as e:
                yield f"data: {json.dumps({'error': str(e)}, ensure_ascii=False)}\n\n"
            finally:
                gen.close()
        return StreamingResponse(sse(), media_type="text/event-stream", headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})

    return r
