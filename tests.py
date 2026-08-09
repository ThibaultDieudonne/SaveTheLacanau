"""
Tests for the Sauver Lacanau game.
Run with: pytest tests.py -v
"""

import pytest
from fastapi.testclient import TestClient

from main import app, ACTIVITIES_DATA
from game import logic
from game.models import (
    PHASE_LOBBY, PHASE_DAY_START, PHASE_ACTIVITY_TIME,
    PHASE_DAY_RESULTS, PHASE_HATE_VOTE, PHASE_ELIMINATION, PHASE_GAME_OVER,
    ROLE_SABOTEUR, ROLE_CHILL_GUY,
)
from game.manager import manager


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def clean_games():
    """Reset game registry before each test."""
    manager._games.clear()
    manager._connections.clear()
    yield
    manager._games.clear()
    manager._connections.clear()


@pytest.fixture
def client():
    return TestClient(app)


def _make_settings(
    player_names=None,
    num_activities=2,
    num_saboteurs=1,
    num_tasks=2,
    sabotage_threshold=3,
    chill_threshold=100,
    elimination_interval=2,
    max_hate_votes=3,
):
    return {
        "game_name": "Test",
        "player_names": player_names or ["Alice", "Bob", "Charlie"],
        "num_activities": num_activities,
        "num_saboteurs": num_saboteurs,
        "num_tasks": num_tasks,
        "sabotage_threshold": sabotage_threshold,
        "chill_threshold": chill_threshold,
        "elimination_interval": elimination_interval,
        "max_hate_votes": max_hate_votes,
    }


def _make_game(settings=None):
    s = settings or _make_settings()
    game = logic.create_game("1234", s, ACTIVITIES_DATA)
    manager.add_game(game)
    return game


# ---------------------------------------------------------------------------
# Logic — settings validation
# ---------------------------------------------------------------------------

class TestValidateSettings:
    def test_valid_settings(self):
        errors = logic.validate_settings(_make_settings(), ACTIVITIES_DATA)
        assert errors == []

    def test_too_few_players(self):
        s = _make_settings(player_names=["Solo", "Duo"])
        errors = logic.validate_settings(s, ACTIVITIES_DATA)
        assert any("joueur" in e for e in errors)

    def test_num_activities_too_high(self):
        s = _make_settings(num_activities=999)
        errors = logic.validate_settings(s, ACTIVITIES_DATA)
        assert any("activité" in e for e in errors)

    def test_num_activities_zero(self):
        s = _make_settings(num_activities=1)
        errors = logic.validate_settings(s, ACTIVITIES_DATA)
        assert any("activité" in e for e in errors)

    def test_too_many_saboteurs(self):
        # 3 players, 2 activities → max saboteurs = 1
        s = _make_settings(player_names=["A", "B", "C"], num_activities=2, num_saboteurs=2)
        errors = logic.validate_settings(s, ACTIVITIES_DATA)
        assert any("saboteur" in e for e in errors)

    def test_num_tasks_too_low(self):
        s = _make_settings(num_tasks=1)
        errors = logic.validate_settings(s, ACTIVITIES_DATA)
        assert any("tâche" in e for e in errors)

    def test_thresholds_zero(self):
        s = _make_settings(sabotage_threshold=0)
        errors = logic.validate_settings(s, ACTIVITIES_DATA)
        assert errors

        s = _make_settings(chill_threshold=0)
        errors = logic.validate_settings(s, ACTIVITIES_DATA)
        assert errors


# ---------------------------------------------------------------------------
# Logic — game creation
# ---------------------------------------------------------------------------

class TestCreateGame:
    def test_correct_player_count(self):
        game = logic.create_game("0001", _make_settings(), ACTIVITIES_DATA)
        assert len(game.players) == 3

    def test_role_assignment(self):
        game = logic.create_game("0001", _make_settings(num_saboteurs=1), ACTIVITIES_DATA)
        roles = [p.role for p in game.players.values()]
        assert roles.count(ROLE_SABOTEUR) == 1
        assert len(game.saboteurs) == 1

    def test_correct_activities_selected(self):
        game = logic.create_game("0001", _make_settings(num_activities=2), ACTIVITIES_DATA)
        assert len(game.available_activities) == 2

    def test_starts_in_lobby(self):
        game = logic.create_game("0001", _make_settings(), ACTIVITIES_DATA)
        assert game.phase == PHASE_LOBBY

    def test_each_player_has_a_role(self):
        game = logic.create_game("0001", _make_settings(), ACTIVITIES_DATA)
        for p in game.players.values():
            assert p.role in (ROLE_SABOTEUR, ROLE_CHILL_GUY) or p.role  # special role is a non-empty string


# ---------------------------------------------------------------------------
# Logic — day lifecycle
# ---------------------------------------------------------------------------

class TestDayLifecycle:
    def test_start_new_day(self):
        game = _make_game()
        logic.start_new_day(game, day_number=1)
        assert game.phase == PHASE_DAY_START
        assert game.current_day.day_number == 1
        assert game.days_since_last_elimination == 1

    def test_transition_to_activity_time(self):
        game = _make_game()
        logic.start_new_day(game, day_number=1)
        activity_name = game.available_activities[0]["name"]
        for name in game.players:
            game.current_day.activity_selections[name] = activity_name
        logic.transition_to_activity_time(game)
        assert game.phase == PHASE_ACTIVITY_TIME
        assert activity_name in game.current_day.activities

    def test_sabotageable_task_not_present_without_saboteurs(self):
        # Put only non-saboteurs in activity
        game = _make_game()
        logic.start_new_day(game, day_number=1)
        activity_name = game.available_activities[0]["name"]
        non_saboteurs = [n for n in game.players if n not in game.saboteurs]
        for name in non_saboteurs:
            game.current_day.activity_selections[name] = activity_name
        logic.transition_to_activity_time(game)
        da = game.current_day.activities[activity_name]
        assert da.sabotageable_task is None

    def test_sabotageable_task_present_with_saboteurs(self):
        # Use 4 players so chill guy can be in activity without special-role holder
        s = _make_settings(player_names=["Alice", "Bob", "Charlie", "Dave"], num_saboteurs=1, num_activities=2)
        game = logic.create_game("0001", s, ACTIVITIES_DATA)
        manager.add_game(game)
        logic.start_new_day(game, day_number=1)
        activity = game.available_activities[0]
        activity_name = activity["name"]
        special_role = activity["special_role"]
        # Only put players that do NOT hold this activity's special role
        non_holders = [n for n, p in game.players.items() if p.role != special_role]
        for name in non_holders:
            game.current_day.activity_selections[name] = activity_name
        logic.transition_to_activity_time(game)
        da = game.current_day.activities.get(activity_name)
        if da is None:
            pytest.skip("No players landed in this activity after role exclusion")
        assert da.sabotageable_task is not None

    def test_special_role_blocks_sabotage(self):
        game = _make_game()
        logic.start_new_day(game, day_number=1)
        activity = game.available_activities[0]
        activity_name = activity["name"]
        special_role = activity["special_role"]
        # Find player with special role for this activity
        role_holder = next(
            (n for n, p in game.players.items() if p.role == special_role), None
        )
        if role_holder is None:
            pytest.skip("No special role holder for first activity in this random seed")
        # Put role holder + saboteur in same activity
        for name in game.players:
            game.current_day.activity_selections[name] = activity_name
        logic.transition_to_activity_time(game)
        da = game.current_day.activities[activity_name]
        assert da.sabotageable_task is None

    def test_process_day_end_successful_task(self):
        game = _make_game()
        logic.start_new_day(game, day_number=1)
        activity_name = game.available_activities[0]["name"]
        for name in game.players:
            game.current_day.activity_selections[name] = activity_name
        logic.transition_to_activity_time(game)
        da = game.current_day.activities[activity_name]

        # Vote everyone for a non-sabotageable task
        safe_task = next(t for t in da.tasks if t.task_name != da.sabotageable_task)
        for name in da.players:
            da.votes[name] = safe_task.task_name

        before_chill = game.chill_counter
        logic.process_day_end(game)
        assert game.chill_counter > before_chill
        assert game.sabotaged_activity_counter == 0
        assert game.phase == PHASE_DAY_RESULTS

    def test_process_day_end_sabotaged_task(self):
        game = _make_game()
        logic.start_new_day(game, day_number=1)
        activity_name = game.available_activities[0]["name"]
        for name in game.players:
            game.current_day.activity_selections[name] = activity_name
        logic.transition_to_activity_time(game)
        da = game.current_day.activities[activity_name]

        if da.sabotageable_task is None:
            pytest.skip("No sabotageable task in this random configuration")

        for name in da.players:
            da.votes[name] = da.sabotageable_task

        before_sabotage = game.sabotaged_activity_counter
        logic.process_day_end(game)
        assert game.sabotaged_activity_counter == before_sabotage + 1
        assert game.phase == PHASE_DAY_RESULTS


# ---------------------------------------------------------------------------
# Logic — win conditions
# ---------------------------------------------------------------------------

class TestWinConditions:
    def test_saboteurs_win(self):
        game = _make_game(_make_settings(sabotage_threshold=1))
        game.sabotaged_activity_counter = 1
        won, side, msg = logic.check_win_condition(game)
        assert won
        assert side == "saboteurs"
        assert msg == "Lacanau est gâché"

    def test_chill_guys_win_by_chill(self):
        game = _make_game(_make_settings(chill_threshold=10))
        game.chill_counter = 10
        won, side, msg = logic.check_win_condition(game)
        assert won
        assert side == "chill_guys"
        assert msg == "Lacanau est un pur banger"

    def test_chill_guys_win_by_elimination(self):
        game = _make_game()
        for name in game.saboteurs:
            game.players[name].is_eliminated = True
        won, side, msg = logic.check_win_condition(game)
        assert won
        assert side == "chill_guys"

    def test_no_winner_mid_game(self):
        game = _make_game()
        won, side, msg = logic.check_win_condition(game)
        assert not won
        assert side is None


# ---------------------------------------------------------------------------
# Logic — hate votes and elimination
# ---------------------------------------------------------------------------

class TestElimination:
    def test_apply_hate_votes(self):
        game = _make_game()
        logic.start_new_day(game, day_number=1)
        players = list(game.players.keys())
        game.current_day.hate_votes = {players[0]: [players[1]], players[2]: [players[1]]}
        logic.apply_hate_votes(game)
        assert game.players[players[1]].hate_counter == 2

    def test_elimination_targets_most_hated(self):
        game = _make_game()
        logic.start_new_day(game, day_number=1)
        target_name = list(game.players.keys())[0]
        game.players[target_name].hate_counter = 99
        name, _ = logic.process_elimination(game)
        assert name == target_name

    def test_saboteur_eliminated_on_reveal(self):
        game = _make_game()
        logic.start_new_day(game, day_number=1)
        saboteur_name = game.saboteurs[0]
        game.players[saboteur_name].hate_counter = 99
        name, was_saboteur = logic.process_elimination(game)
        assert name == saboteur_name
        assert was_saboteur
        assert game.players[saboteur_name].is_eliminated

    def test_innocent_not_eliminated(self):
        game = _make_game()
        logic.start_new_day(game, day_number=1)
        innocent = next(n for n in game.players if n not in game.saboteurs)
        game.players[innocent].hate_counter = 99
        name, was_saboteur = logic.process_elimination(game)
        assert name == innocent
        assert not was_saboteur
        assert not game.players[innocent].is_eliminated

    def test_should_eliminate_trigger(self):
        game = _make_game(_make_settings(elimination_interval=2))
        game.days_since_last_elimination = 2
        assert logic.should_eliminate(game)
        game.days_since_last_elimination = 1
        assert not logic.should_eliminate(game)


# ---------------------------------------------------------------------------
# HTTP — pages render without error
# ---------------------------------------------------------------------------

class TestHTTPPages:
    def test_home_page(self, client):
        r = client.get("/")
        assert r.status_code == 200
        assert "Sauver Lacanau" in r.text

    def test_new_game_page(self, client):
        r = client.get("/nouvelle-partie")
        assert r.status_code == 200
        assert "Nouvelle Partie" in r.text

    def test_join_game_page(self, client):
        r = client.get("/rejoindre-partie")
        assert r.status_code == 200

    def test_terminate_page_empty(self, client):
        r = client.get("/terminer-partie")
        assert r.status_code == 200
        assert "Aucune partie active" in r.text

    def test_join_unknown_game(self, client):
        r = client.post("/rejoindre-partie", data={"game_id": "9999"})
        assert r.status_code == 200
        assert "identifiant" in r.text.lower()

    def test_create_game_flow(self, client):
        r = client.post(
            "/nouvelle-partie",
            data={
                "game_name": "Test Game",
                "player_names": ["Alice", "Bob", "Charlie"],
                "num_activities": "2",
                "num_saboteurs": "1",
                "num_tasks": "2",
                "sabotage_threshold": "3",
                "chill_threshold": "100",
                "elimination_interval": "2",
                "max_hate_votes": "3",
            },
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert "/rejoindre" in r.headers["location"]

    def test_create_game_invalid(self, client):
        # Only 2 player names → our validation rejects it (min is 3)
        r = client.post(
            "/nouvelle-partie",
            data={
                "game_name": "TestGame",
                "player_names": ["Alice", "Bob"],
                "num_activities": "2",
                "num_saboteurs": "1",
                "num_tasks": "2",
                "sabotage_threshold": "3",
                "chill_threshold": "100",
                "elimination_interval": "2",
                "max_hate_votes": "3",
            },
        )
        assert r.status_code == 200
        assert "joueur" in r.text

    def test_player_select_page(self, client):
        game = _make_game()
        r = client.get(f"/parties/{game.game_id}/rejoindre")
        assert r.status_code == 200
        for name in game.players:
            assert name in r.text

    def test_claim_player_transitions_to_game(self, client):
        game = _make_game()
        name = list(game.players.keys())[0]
        r = client.post(
            f"/parties/{game.game_id}/rejoindre/{name}",
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert name in r.headers["location"]
        assert name in game.players_joined

    def test_game_page_lobby(self, client):
        game = _make_game()
        name = list(game.players.keys())[0]
        game.players_joined.add(name)
        r = client.get(f"/parties/{game.game_id}/{name}")
        assert r.status_code == 200
        # Should show lobby/waiting screen
        assert name in r.text

    def test_game_starts_when_all_join(self, client):
        game = _make_game()
        names = list(game.players.keys())
        # All players but the last one join
        for name in names[:-1]:
            client.post(
                f"/parties/{game.game_id}/rejoindre/{name}",
                follow_redirects=False,
            )
        assert game.phase == PHASE_LOBBY
        # Last player joins → game auto-starts
        client.post(
            f"/parties/{game.game_id}/rejoindre/{names[-1]}",
            follow_redirects=False,
        )
        assert game.phase == PHASE_DAY_START

    def test_terminate_game(self, client):
        game = _make_game()
        gid = game.game_id
        r = client.post(f"/terminer-partie/{gid}", follow_redirects=False)
        assert r.status_code == 303
        assert manager.get_game(gid) is None
