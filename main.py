import json
import os
import random
import string
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Form, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from urllib.parse import quote

from game.manager import manager
from game.models import (
    GameState,
    PHASE_LOBBY, PHASE_DAY_START, PHASE_ACTIVITY_TIME,
    PHASE_DAY_RESULTS, PHASE_HATE_VOTE, PHASE_ELIMINATION, PHASE_GAME_OVER,
    ROLE_SABOTEUR, ROLE_CHILL_GUY,
)
from game import logic

# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------

BASE_DIR = Path(__file__).parent
ACTIVITIES_PATH = BASE_DIR / "activities.json"
CONFIGS_DIR = BASE_DIR / "data" / "saved_configs"
CONFIGS_DIR.mkdir(parents=True, exist_ok=True)

with ACTIVITIES_PATH.open(encoding="utf-8") as f:
    ACTIVITIES_DATA: list = json.load(f)["activities"]

app = FastAPI()
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
templates.env.filters["urlencode"] = lambda s: quote(str(s), safe="")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _generate_game_id() -> str:
    existing = {g.game_id for g in manager.list_games()}
    while True:
        gid = "".join(random.choices(string.digits, k=4))
        if gid not in existing:
            return gid


def _load_saved_configs() -> list[dict]:
    configs = []
    for path in sorted(CONFIGS_DIR.glob("*.json"), reverse=True):
        try:
            with path.open(encoding="utf-8") as f:
                data = json.load(f)
            data["config_id"] = path.stem
            configs.append(data)
        except Exception:
            pass
    return configs


def _save_config(settings: dict) -> None:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_name = "".join(c if c.isalnum() or c in "-_ " else "_" for c in settings["game_name"])
    filename = f"{timestamp}_{safe_name}.json"
    with (CONFIGS_DIR / filename).open("w", encoding="utf-8") as f:
        json.dump(settings, f, ensure_ascii=False, indent=2)


def _days_until_elimination(game) -> int:
    interval = game.settings.get("elimination_interval", 3)
    return max(0, interval - game.days_since_last_elimination)


def _render_game_page(request: Request, game_id: str, player_name: str):
    game = manager.get_game(game_id)
    if game is None:
        raise HTTPException(status_code=404, detail="Partie introuvable")
    if player_name not in game.players:
        raise HTTPException(status_code=404, detail="Joueur introuvable")

    player = game.players[player_name]
    sabotage_threshold = game.settings.get("sabotage_threshold", 5)
    chill_threshold = game.settings.get("chill_threshold", 500)

    ctx = {
        "game": game,
        "player": player,
        "player_name": player_name,
        "game_id": game_id,
        "sabotage_threshold": sabotage_threshold,
        "chill_threshold": chill_threshold,
        "days_until_elimination": _days_until_elimination(game),
        "active_players": logic.get_active_players(game),
        "role_to_activity": {a["special_role"]: a["name"] for a in game.available_activities},
    }

    phase = game.phase

    if phase == PHASE_LOBBY:
        return templates.TemplateResponse(request, "game/lobby.html", ctx)
    if phase == PHASE_DAY_START:
        return templates.TemplateResponse(request, "game/day_start.html", ctx)
    if phase == PHASE_ACTIVITY_TIME:
        day = game.current_day
        # find this player's activity and its state
        player_activity_name = day.activity_selections.get(player_name)
        player_activity = day.activities.get(player_activity_name) if player_activity_name else None
        ctx["player_activity_name"] = player_activity_name
        ctx["player_activity"] = player_activity
        return templates.TemplateResponse(request, "game/activity_time.html", ctx)
    if phase == PHASE_DAY_RESULTS:
        return templates.TemplateResponse(request, "game/day_results.html", ctx)
    if phase == PHASE_HATE_VOTE:
        # players that can be voted against: active, non-eliminated, not self
        votable = [
            p for p in logic.get_active_players(game)
            if p.name != player_name
        ]
        ctx["votable_players"] = votable
        return templates.TemplateResponse(request, "game/hate_vote.html", ctx)
    if phase == PHASE_ELIMINATION:
        return templates.TemplateResponse(request, "game/elimination.html", ctx)
    if phase == PHASE_GAME_OVER:
        return templates.TemplateResponse(request, "game/game_over.html", ctx)

    raise HTTPException(status_code=500, detail="Phase inconnue")


async def _broadcast_refresh(game_id: str) -> None:
    await manager.broadcast(game_id, {"type": "refresh"})


# ---------------------------------------------------------------------------
# Main pages
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(request, "index.html", {})


# ---------------------------------------------------------------------------
# New game
# ---------------------------------------------------------------------------

@app.get("/nouvelle-partie", response_class=HTMLResponse)
async def new_game_page(request: Request, config_id: Optional[str] = None):
    configs = _load_saved_configs()
    prefill = None
    if config_id:
        prefill = next((c for c in configs if c["config_id"] == config_id), None)

    min_tasks_possible = min(len(a["tasks"]) for a in ACTIVITIES_DATA)
    return templates.TemplateResponse(request, "new_game.html", {
        "saved_configs": configs,
        "prefill": prefill,
        "num_activities_in_json": len(ACTIVITIES_DATA),
        "min_tasks_possible": min_tasks_possible,
    })


@app.post("/nouvelle-partie", response_class=HTMLResponse)
async def create_game(
    request: Request,
    game_name: str = Form(...),
    player_names: list[str] = Form(...),
    num_activities: int = Form(3),
    num_saboteurs: int = Form(2),
    num_tasks: int = Form(3),
    sabotage_threshold: int = Form(5),
    chill_threshold: int = Form(500),
    elimination_interval: int = Form(3),
):
    clean_names = [n.strip() for n in player_names if n.strip()]
    settings = {
        "game_name": game_name.strip(),
        "player_names": clean_names,
        "num_activities": num_activities,
        "num_saboteurs": num_saboteurs,
        "num_tasks": num_tasks,
        "sabotage_threshold": sabotage_threshold,
        "chill_threshold": chill_threshold,
        "elimination_interval": elimination_interval,
    }

    errors = logic.validate_settings(settings, ACTIVITIES_DATA)
    if errors or not settings["game_name"]:
        if not settings["game_name"]:
            errors.insert(0, "Le nom de la partie est obligatoire.")
        min_tasks_possible = min(len(a["tasks"]) for a in ACTIVITIES_DATA)
        return templates.TemplateResponse(request, "new_game.html", {
            "errors": errors,
            "saved_configs": _load_saved_configs(),
            "prefill": settings,
            "num_activities_in_json": len(ACTIVITIES_DATA),
            "min_tasks_possible": min_tasks_possible,
        })

    _save_config(settings)
    game_id = _generate_game_id()
    game = logic.create_game(game_id, settings, ACTIVITIES_DATA)
    manager.add_game(game)

    return RedirectResponse(f"/parties/{game_id}/rejoindre", status_code=303)


# ---------------------------------------------------------------------------
# Join game
# ---------------------------------------------------------------------------

@app.get("/rejoindre-partie", response_class=HTMLResponse)
async def join_game_page(request: Request):
    return templates.TemplateResponse(request, "join_game.html", {})


@app.post("/rejoindre-partie", response_class=HTMLResponse)
async def join_game(request: Request, game_id: str = Form(...)):
    game = manager.get_game(game_id.strip())
    if game is None:
        return templates.TemplateResponse(request, "join_game.html", {
            "error": "Aucune partie active avec cet identifiant.",
        })
    return RedirectResponse(f"/parties/{game_id.strip()}/rejoindre", status_code=303)


# ---------------------------------------------------------------------------
# Terminate game
# ---------------------------------------------------------------------------

@app.get("/terminer-partie", response_class=HTMLResponse)
async def terminate_page(request: Request):
    return templates.TemplateResponse(request, "terminate.html", {
        "games": manager.list_games(),
    })


@app.post("/terminer-partie/{game_id}")
async def terminate_game(game_id: str):
    manager.terminate_game(game_id)
    return RedirectResponse("/terminer-partie", status_code=303)


# ---------------------------------------------------------------------------
# Player name selection (lobby)
# ---------------------------------------------------------------------------

@app.get("/parties/{game_id}/rejoindre", response_class=HTMLResponse)
async def player_select_page(request: Request, game_id: str):
    game = manager.get_game(game_id)
    if game is None:
        raise HTTPException(status_code=404, detail="Partie introuvable")
    return templates.TemplateResponse(request, "player_select.html", {
        "game": game,
        "game_id": game_id,
    })


@app.post("/parties/{game_id}/rejoindre/{player_name}")
async def claim_player(game_id: str, player_name: str):
    game = manager.get_game(game_id)
    if game is None:
        raise HTTPException(status_code=404, detail="Partie introuvable")
    if player_name not in game.players:
        raise HTTPException(status_code=404, detail="Joueur introuvable")

    # Mark the name as joined (idempotent for reconnection)
    game.players_joined.add(player_name)

    # Auto-start the game when everyone has joined
    if game.phase == PHASE_LOBBY and game.players_joined >= set(game.players.keys()):
        logic.start_new_day(game, day_number=1)
        await _broadcast_refresh(game_id)

    return RedirectResponse(f"/parties/{game_id}/{player_name}", status_code=303)


# ---------------------------------------------------------------------------
# Game page (per player)
# ---------------------------------------------------------------------------

@app.get("/parties/{game_id}/{player_name}", response_class=HTMLResponse)
async def game_page(request: Request, game_id: str, player_name: str):
    return _render_game_page(request, game_id, player_name)


# ---------------------------------------------------------------------------
# Game actions — day_start: select activity
# ---------------------------------------------------------------------------

@app.post("/parties/{game_id}/{player_name}/choisir-activite")
async def select_activity(game_id: str, player_name: str, activity: str = Form(...)):
    game = manager.get_game(game_id)
    if game is None or game.phase != PHASE_DAY_START:
        raise HTTPException(status_code=400)

    player = game.players.get(player_name)
    if player is None or player.is_eliminated:
        raise HTTPException(status_code=400)

    # Prevent re-selection of last day's activity
    if player.last_activity and player.last_activity == activity:
        raise HTTPException(status_code=400, detail="Même activité qu'hier")

    game.current_day.activity_selections[player_name] = activity

    active = logic.get_active_players(game)
    all_selected = all(p.name in game.current_day.activity_selections for p in active)
    if all_selected:
        logic.transition_to_activity_time(game)
        await _broadcast_refresh(game_id)

    return RedirectResponse(f"/parties/{game_id}/{player_name}", status_code=303)


# ---------------------------------------------------------------------------
# Game actions — activity_time: vote for a task
# ---------------------------------------------------------------------------

@app.post("/parties/{game_id}/{player_name}/voter-tache")
async def vote_task(game_id: str, player_name: str, task_name: str = Form(...)):
    game = manager.get_game(game_id)
    if game is None or game.phase != PHASE_ACTIVITY_TIME:
        raise HTTPException(status_code=400)

    player = game.players.get(player_name)
    if player is None or player.is_eliminated:
        raise HTTPException(status_code=400)

    activity_name = game.current_day.activity_selections.get(player_name)
    if not activity_name:
        raise HTTPException(status_code=400)

    day_activity = game.current_day.activities.get(activity_name)
    if not day_activity:
        raise HTTPException(status_code=400)

    day_activity.votes[player_name] = task_name

    # Transition when every active player who has an activity has voted
    active = logic.get_active_players(game)
    expected_voters = {
        p.name for p in active
        if game.current_day.activity_selections.get(p.name) in game.current_day.activities
    }
    actual_voters: set[str] = set()
    for da in game.current_day.activities.values():
        actual_voters.update(da.votes.keys())

    if actual_voters >= expected_voters:
        logic.process_day_end(game)
        won, winner, msg = logic.check_win_condition(game)
        if won:
            logic.set_game_over(game, winner, msg)
        await _broadcast_refresh(game_id)

    return RedirectResponse(f"/parties/{game_id}/{player_name}", status_code=303)


# ---------------------------------------------------------------------------
# Game actions — day_results: ready to continue
# ---------------------------------------------------------------------------

@app.post("/parties/{game_id}/{player_name}/continuer")
async def player_ready(game_id: str, player_name: str):
    game = manager.get_game(game_id)
    if game is None or game.phase != PHASE_DAY_RESULTS:
        raise HTTPException(status_code=400)

    game.current_day.continue_ready.add(player_name)

    active = logic.get_active_players(game)
    if game.current_day.continue_ready >= {p.name for p in active}:
        # Hate vote triggers if any sabotage has occurred so far (including today)
        if game.sabotaged_activity_counter > 0:
            game.phase = PHASE_HATE_VOTE
        else:
            _advance_to_next_day_or_elimination(game)
        await _broadcast_refresh(game_id)

    return RedirectResponse(f"/parties/{game_id}/{player_name}", status_code=303)


# ---------------------------------------------------------------------------
# Game actions — hate_vote: submit suspicion votes
# ---------------------------------------------------------------------------

@app.post("/parties/{game_id}/{player_name}/voter-suspicion")
async def vote_suspicion(
    request: Request,
    game_id: str,
    player_name: str,
    suspects: list[str] = Form(default=[]),
):
    game = manager.get_game(game_id)
    if game is None or game.phase != PHASE_HATE_VOTE:
        raise HTTPException(status_code=400)

    player = game.players.get(player_name)
    if player is None or player.is_eliminated:
        raise HTTPException(status_code=400)

    game.current_day.hate_votes[player_name] = suspects
    game.current_day.hate_votes_submitted.add(player_name)

    active = logic.get_active_players(game)
    if game.current_day.hate_votes_submitted >= {p.name for p in active}:
        logic.apply_hate_votes(game)
        if logic.should_eliminate(game):
            logic.process_elimination(game)
            won, winner, msg = logic.check_win_condition(game)
            if won:
                logic.set_game_over(game, winner, msg)
        else:
            _advance_to_next_day(game)
        await _broadcast_refresh(game_id)

    return RedirectResponse(f"/parties/{game_id}/{player_name}", status_code=303)


# ---------------------------------------------------------------------------
# Game actions — elimination: continue after reveal
# ---------------------------------------------------------------------------

@app.post("/parties/{game_id}/{player_name}/continuer-elimination")
async def continue_after_elimination(game_id: str, player_name: str):
    game = manager.get_game(game_id)
    if game is None or game.phase != PHASE_ELIMINATION:
        raise HTTPException(status_code=400)

    game.current_day.elim_continue_ready.add(player_name)

    active = logic.get_active_players(game)
    # Eliminated players are also shown the screen; everyone must click
    all_players = set(game.players.keys())
    if game.current_day.elim_continue_ready >= all_players:
        won, winner, msg = logic.check_win_condition(game)
        if won:
            logic.set_game_over(game, winner, msg)
        else:
            _advance_to_next_day(game)
        await _broadcast_refresh(game_id)

    return RedirectResponse(f"/parties/{game_id}/{player_name}", status_code=303)


# ---------------------------------------------------------------------------
# Internal helpers for day progression
# ---------------------------------------------------------------------------

def _advance_to_next_day_or_elimination(game: GameState) -> None:
    if logic.should_eliminate(game):
        logic.process_elimination(game)
        won, winner, msg = logic.check_win_condition(game)
        if won:
            logic.set_game_over(game, winner, msg)
    else:
        _advance_to_next_day(game)


def _advance_to_next_day(game: GameState) -> None:
    next_day = (game.current_day.day_number + 1) if game.current_day else 1
    logic.start_new_day(game, day_number=next_day)


# ---------------------------------------------------------------------------
# WebSocket endpoint
# ---------------------------------------------------------------------------

@app.websocket("/ws/{game_id}/{player_name}")
async def websocket_endpoint(websocket: WebSocket, game_id: str, player_name: str):
    game = manager.get_game(game_id)
    if game is None or player_name not in game.players:
        await websocket.close(code=4004)
        return

    await manager.connect(game_id, websocket)
    try:
        while True:
            # Keep connection alive; we only push from server side
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(game_id, websocket)
