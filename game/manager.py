import asyncio
from typing import Optional
from fastapi import WebSocket

from .models import GameState


class GameManager:
    def __init__(self) -> None:
        self._games: dict[str, GameState] = {}
        # game_id -> list of active WebSocket connections
        self._connections: dict[str, list[WebSocket]] = {}

    # ------------------------------------------------------------------
    # Game registry
    # ------------------------------------------------------------------

    def add_game(self, game: GameState) -> None:
        self._games[game.game_id] = game
        self._connections.setdefault(game.game_id, [])

    def get_game(self, game_id: str) -> Optional[GameState]:
        return self._games.get(game_id)

    def terminate_game(self, game_id: str) -> None:
        self._games.pop(game_id, None)
        self._connections.pop(game_id, None)

    def list_games(self) -> list[GameState]:
        return list(self._games.values())

    # ------------------------------------------------------------------
    # WebSocket management
    # ------------------------------------------------------------------

    async def connect(self, game_id: str, websocket: WebSocket) -> None:
        await websocket.accept()
        self._connections.setdefault(game_id, []).append(websocket)

    def disconnect(self, game_id: str, websocket: WebSocket) -> None:
        conns = self._connections.get(game_id, [])
        if websocket in conns:
            conns.remove(websocket)

    async def broadcast(self, game_id: str, message: dict) -> None:
        conns = list(self._connections.get(game_id, []))
        dead: list[WebSocket] = []
        for ws in conns:
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(game_id, ws)


# Singleton used throughout the application
manager = GameManager()
