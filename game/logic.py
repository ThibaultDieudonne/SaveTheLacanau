import random
from typing import Optional

from .models import (
    GameState, DayState, DayActivityState, TaskState, PlayerState,
    PHASE_LOBBY, PHASE_DAY_START, PHASE_ACTIVITY_TIME,
    PHASE_DAY_RESULTS, PHASE_HATE_VOTE, PHASE_ELIMINATION, PHASE_GAME_OVER,
    ROLE_SABOTEUR, ROLE_CHILL_GUY,
)


# ---------------------------------------------------------------------------
# Game creation
# ---------------------------------------------------------------------------

def create_game(game_id: str, settings: dict, activities_data: list) -> GameState:
    player_names = settings["player_names"]
    num_activities = settings["num_activities"]
    num_saboteurs = settings["num_saboteurs"]

    selected_activities = random.sample(
        activities_data, min(num_activities, len(activities_data))
    )

    shuffled = player_names.copy()
    random.shuffle(shuffled)

    saboteurs = shuffled[:num_saboteurs]
    remaining = shuffled[num_saboteurs:]

    players: dict[str, PlayerState] = {}
    for name in saboteurs:
        players[name] = PlayerState(name=name, role=ROLE_SABOTEUR)

    for i, activity in enumerate(selected_activities):
        if i < len(remaining):
            players[remaining[i]] = PlayerState(name=remaining[i], role=activity["special_role"])

    for name in remaining[len(selected_activities):]:
        players[name] = PlayerState(name=name, role=ROLE_CHILL_GUY)

    return GameState(
        game_id=game_id,
        game_name=settings["game_name"],
        settings=settings,
        phase=PHASE_LOBBY,
        players=players,
        saboteurs=saboteurs,
        available_activities=selected_activities,
        current_day=None,
    )


# ---------------------------------------------------------------------------
# Day lifecycle
# ---------------------------------------------------------------------------

def get_active_players(game: GameState) -> list[PlayerState]:
    return [p for p in game.players.values() if not p.is_eliminated]


def start_new_day(game: GameState, day_number: int) -> None:
    game.sabotage_counter_at_day_start = game.sabotaged_activity_counter
    game.days_since_last_elimination += 1
    game.current_day = DayState(day_number=day_number)
    game.phase = PHASE_DAY_START


def transition_to_activity_time(game: GameState) -> None:
    day = game.current_day
    num_tasks = game.settings.get("num_tasks", 3)

    activity_players: dict[str, list[str]] = {}
    for player_name, activity_name in day.activity_selections.items():
        activity_players.setdefault(activity_name, []).append(player_name)

    for activity_data in game.available_activities:
        activity_name = activity_data["name"]
        players_here = activity_players.get(activity_name, [])
        if not players_here:
            continue

        count = min(num_tasks, len(activity_data["tasks"]))
        selected_task_data = random.sample(activity_data["tasks"], count)

        task_states: list[TaskState] = []
        for td in selected_task_data:
            points_per_player = {
                p: random.randint(td["min_points_awarded"], td["max_points_awarded"])
                for p in players_here
            }
            task_states.append(TaskState(
                task_name=td["name"],
                failure_reason=td["failure_reason"],
                min_points=td["min_points_awarded"],
                max_points=td["max_points_awarded"],
                points_per_player=points_per_player,
            ))

        special_role_holder = _find_special_role_holder(
            game, activity_data["special_role"], players_here
        )
        sabotageable = _determine_sabotageable_task(
            task_states, players_here, game.saboteurs, special_role_holder
        )

        day.activities[activity_name] = DayActivityState(
            activity_name=activity_name,
            special_role=activity_data["special_role"],
            players=players_here,
            tasks=task_states,
            sabotageable_task=sabotageable,
        )

    game.phase = PHASE_ACTIVITY_TIME


def _find_special_role_holder(
    game: GameState, special_role: str, players_in_activity: list[str]
) -> Optional[str]:
    for name in players_in_activity:
        if game.players[name].role == special_role:
            return name
    return None


def _determine_sabotageable_task(
    task_states: list[TaskState],
    players_in_activity: list[str],
    saboteurs: list[str],
    special_role_holder: Optional[str],
) -> Optional[str]:
    if special_role_holder and special_role_holder in players_in_activity:
        return None

    saboteurs_here = [p for p in players_in_activity if p in saboteurs]
    non_saboteurs_here = [p for p in players_in_activity if p not in saboteurs]

    if not saboteurs_here:
        return None

    if not non_saboteurs_here:
        return random.choice([t.task_name for t in task_states])

    weights = [
        sum(t.points_per_player.get(p, 0) for p in non_saboteurs_here)
        for t in task_states
    ]
    total = sum(weights)
    if total == 0:
        return random.choice([t.task_name for t in task_states])

    return random.choices([t.task_name for t in task_states], weights=weights, k=1)[0]


# ---------------------------------------------------------------------------
# End-of-day processing
# ---------------------------------------------------------------------------

def process_day_end(game: GameState) -> None:
    """Resolve votes for all activities, update counters, build day_results."""
    day = game.current_day
    results = []
    new_sabotages = 0

    for activity_name, da in day.activities.items():
        if not da.players:
            continue

        chosen, sabotaged, failure_reason, chill_pts = _resolve_activity_votes(da)
        da.chosen_task = chosen
        da.was_sabotaged = sabotaged
        da.failure_reason = failure_reason
        da.chill_points_earned = chill_pts

        if sabotaged:
            game.sabotaged_activity_counter += 1
            new_sabotages += 1
        else:
            game.chill_counter += chill_pts

        results.append({
            "activity_name": activity_name,
            "chosen_task": chosen,
            "was_sabotaged": sabotaged,
            "failure_reason": failure_reason,
            "chill_points": chill_pts,
        })

    day.day_results = results
    day.new_sabotages = new_sabotages

    # Update last_activity for each player
    for player_name, activity_name in day.activity_selections.items():
        game.players[player_name].last_activity = activity_name

    game.phase = PHASE_DAY_RESULTS


def _resolve_activity_votes(da: DayActivityState) -> tuple:
    if not da.votes:
        return None, False, None, 0

    vote_counts: dict[str, int] = {}
    for task_name in da.votes.values():
        vote_counts[task_name] = vote_counts.get(task_name, 0) + 1

    max_votes = max(vote_counts.values())
    top_tasks = [t for t, v in vote_counts.items() if v == max_votes]
    chosen = random.choice(top_tasks)

    was_sabotaged = chosen == da.sabotageable_task
    failure_reason = None
    chill_pts = 0

    task_obj = next((t for t in da.tasks if t.task_name == chosen), None)
    if was_sabotaged and task_obj:
        failure_reason = task_obj.failure_reason
    elif not was_sabotaged and task_obj:
        chill_pts = sum(task_obj.points_per_player.get(p, 0) for p in da.players)

    return chosen, was_sabotaged, failure_reason, chill_pts


# ---------------------------------------------------------------------------
# Win-condition checks
# ---------------------------------------------------------------------------

def check_win_condition(game: GameState) -> tuple[bool, Optional[str], Optional[str]]:
    """Returns (has_won, winner_side, message) or (False, None, None)."""
    sabotage_threshold = game.settings.get("sabotage_threshold", 5)
    chill_threshold = game.settings.get("chill_threshold", 500)

    if game.sabotaged_activity_counter >= sabotage_threshold:
        return True, "saboteurs", "Lacanau est gâché"

    if game.chill_counter >= chill_threshold:
        return True, "chill_guys", "Lacanau est un pur banger"

    active_saboteurs = [s for s in game.saboteurs if not game.players[s].is_eliminated]
    if not active_saboteurs:
        return True, "chill_guys", "Lacanau est un pur banger"

    return False, None, None


def set_game_over(game: GameState, winner: str, message: str) -> None:
    game.winner = winner
    game.winner_message = message
    game.phase = PHASE_GAME_OVER


# ---------------------------------------------------------------------------
# Hate-vote and elimination
# ---------------------------------------------------------------------------

def apply_hate_votes(game: GameState) -> None:
    day = game.current_day
    for voted_players in day.hate_votes.values():
        for name in voted_players:
            if name in game.players:
                game.players[name].hate_counter += 1


def should_eliminate(game: GameState) -> bool:
    elim_interval = game.settings.get("elimination_interval", 3)
    return game.days_since_last_elimination >= elim_interval


def process_elimination(game: GameState) -> tuple[str, bool]:
    """Pick most-hated active player, eliminate if saboteur. Returns (name, was_saboteur)."""
    active = [p for p in game.players.values() if not p.is_eliminated]
    if not active:
        return None, False

    max_hate = max(p.hate_counter for p in active)
    top = [p for p in active if p.hate_counter == max_hate]
    target = random.choice(top)

    was_saboteur = target.name in game.saboteurs
    if was_saboteur:
        target.is_eliminated = True
    else:
        target.hate_counter = 0

    game.current_day.elimination_player = target.name
    game.current_day.elimination_was_saboteur = was_saboteur
    game.days_since_last_elimination = 0
    game.phase = PHASE_ELIMINATION

    return target.name, was_saboteur


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

def validate_settings(settings: dict, activities_data: list) -> list[str]:
    """Return list of error strings; empty list means valid."""
    errors = []
    player_names = [n.strip() for n in settings.get("player_names", []) if n.strip()]
    if len(player_names) < 2:
        errors.append("Il faut au moins 2 joueurs.")

    num_activities = settings.get("num_activities", 3)
    if num_activities < 1:
        errors.append("Le nombre d'activités doit être au moins 1.")
    if num_activities > len(activities_data):
        errors.append(f"Le nombre d'activités ne peut pas dépasser {len(activities_data)}.")

    num_saboteurs = settings.get("num_saboteurs", 2)
    max_saboteurs = max(1, len(player_names) - num_activities)
    if num_saboteurs < 1:
        errors.append("Il faut au moins 1 saboteur.")
    if num_saboteurs > max_saboteurs:
        errors.append(
            f"Le nombre de saboteurs ne peut pas dépasser {max_saboteurs} "
            "(nombre de joueurs − nombre d'activités)."
        )

    min_task_count = min(len(a["tasks"]) for a in activities_data) if activities_data else 0
    num_tasks = settings.get("num_tasks", 3)
    if num_tasks < 2:
        errors.append("Il faut au moins 2 tâches par activité.")
    if num_tasks > min_task_count:
        errors.append(f"Le nombre de tâches ne peut pas dépasser {min_task_count}.")

    if settings.get("sabotage_threshold", 5) < 1:
        errors.append("Le nombre de tâches à saboter doit être au moins 1.")
    if settings.get("chill_threshold", 500) < 1:
        errors.append("Le max chill doit être au moins 1.")
    if settings.get("elimination_interval", 3) < 1:
        errors.append("L'intervalle d'élimination doit être au moins 1.")

    return errors
