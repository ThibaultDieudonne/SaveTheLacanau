from dataclasses import dataclass, field
from typing import Optional

# Game phase constants
PHASE_LOBBY = "lobby"
PHASE_DAY_START = "day_start"
PHASE_ACTIVITY_TIME = "activity_time"
PHASE_DAY_RESULTS = "day_results"
PHASE_HATE_VOTE = "hate_vote"
PHASE_ELIMINATION = "elimination"
PHASE_GAME_OVER = "game_over"

ROLE_SABOTEUR = "saboteur"
ROLE_CHILL_GUY = "chill_guy"


@dataclass
class TaskState:
    task_name: str
    failure_reason: str
    min_points: int
    max_points: int
    # randomised chill points per player for this task
    points_per_player: dict = field(default_factory=dict)


@dataclass
class DayActivityState:
    activity_name: str
    special_role: str
    players: list = field(default_factory=list)      # player names present
    tasks: list = field(default_factory=list)         # list[TaskState]
    sabotageable_task: Optional[str] = None           # task_name or None
    votes: dict = field(default_factory=dict)         # player_name -> task_name
    chosen_task: Optional[str] = None
    was_sabotaged: bool = False
    failure_reason: Optional[str] = None
    chill_points_earned: int = 0


@dataclass
class PlayerState:
    name: str
    role: str                          # ROLE_SABOTEUR, ROLE_CHILL_GUY, or special role name
    is_eliminated: bool = False
    hate_counter: int = 0
    last_activity: Optional[str] = None


@dataclass
class DayState:
    day_number: int
    # day_start sub-state
    activity_selections: dict = field(default_factory=dict)   # player_name -> activity_name
    # activity_time sub-state
    activities: dict = field(default_factory=dict)            # activity_name -> DayActivityState
    # day_results sub-state
    continue_ready: set = field(default_factory=set)
    day_results: list = field(default_factory=list)           # processed result dicts
    new_sabotages: int = 0
    # hate_vote sub-state
    hate_votes: dict = field(default_factory=dict)            # voter_name -> list[player_name]
    hate_votes_submitted: set = field(default_factory=set)
    # elimination sub-state
    elimination_player: Optional[str] = None
    elimination_was_saboteur: bool = False
    elim_continue_ready: set = field(default_factory=set)


@dataclass
class GameState:
    game_id: str
    game_name: str
    settings: dict
    phase: str
    players: dict                    # name -> PlayerState
    saboteurs: list                  # list of saboteur names
    available_activities: list       # activity dicts from activities.json
    current_day: Optional[DayState]
    chill_counter: int = 0
    sabotaged_activity_counter: int = 0
    # snapshot taken at start of each day to detect new sabotages for hate-vote gating
    sabotage_counter_at_day_start: int = 0
    winner: Optional[str] = None
    winner_message: Optional[str] = None
    # resets to 0 after each elimination event
    days_since_last_elimination: int = 0
    history: list = field(default_factory=list)   # list of DayResult dicts
    players_joined: set = field(default_factory=set)
