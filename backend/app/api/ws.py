import json
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect

from app.api.deps import get_current_user
from app.core.database import get_db
from app.core.security import decode_token

router = APIRouter(tags=["websocket"])


class ConnectionManager:
    def __init__(self):
        # role -> set of websockets
        self._connections: dict[str, set[WebSocket]] = {}
        # ws -> (user_id, role)
        self._meta: dict[WebSocket, tuple[str, str]] = {}

    async def connect(self, ws: WebSocket, user_id: str, role: str) -> None:
        await ws.accept()
        self._connections.setdefault(role, set()).add(ws)
        self._connections.setdefault("all", set()).add(ws)
        self._meta[ws] = (user_id, role)

    def disconnect(self, ws: WebSocket) -> None:
        meta = self._meta.pop(ws, None)
        if meta:
            _, role = meta
            self._connections.get(role, set()).discard(ws)
            self._connections.get("all", set()).discard(ws)

    async def broadcast(self, message: dict, roles: list[str] | None = None) -> None:
        targets: set[WebSocket] = set()
        if roles:
            for role in roles:
                targets |= self._connections.get(role, set())
        else:
            targets = self._connections.get("all", set())

        dead: list[WebSocket] = []
        payload = json.dumps(message)
        for ws in targets:
            try:
                await ws.send_text(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)

    @property
    def connection_count(self) -> int:
        return len(self._meta)


manager = ConnectionManager()


@router.websocket("/ws")
async def websocket_endpoint(
    websocket: WebSocket,
    token: str = Query(...),
):
    payload = decode_token(token)
    if not payload or payload.get("type") != "access":
        await websocket.close(code=4001)
        return

    user_id = payload.get("sub", "")
    role = payload.get("role", "viewer")

    await manager.connect(websocket, user_id, role)
    try:
        while True:
            # Keep connection alive; client can send ping
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        manager.disconnect(websocket)
