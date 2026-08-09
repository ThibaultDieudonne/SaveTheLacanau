# Sauver Lacanau

A real-time in-person party game served as a web application. Players join from their phones via a shared Wi-Fi network.

## Requirements

- Python 3.11+

## Setup

```bash
pip install -r requirements.txt
```

## Running

**Local (development) — phones on the same Wi-Fi**
```bash
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```
Access at `http://<your-local-ip>:8000`. Find your IP with `ipconfig` on Windows.

Kill the local process:
```bash
Get-Process | Where-Object { $_.Name -like "python*" } | Stop-Process -Force
```

**Production**
```bash
uvicorn main:app --host 0.0.0.0 --port 8000 --workers 1
```
> `--workers 1` is required — game state lives in memory and must not be split across processes.

## Game data

Activities are defined in [`activities.json`](activities.json). Replace the placeholder data with your own following the same structure:

```json
{
  "activities": [
    {
      "name": "...",
      "special_role": "...",
      "tasks": [
        {
          "name": "...",
          "failure_reason": "...",
          "min_points_awarded": 10,
          "max_points_awarded": 50
        }
      ]
    }
  ]
}
```

Saved game configurations are stored in `data/saved_configs/` (created automatically at first run).

## Project structure

```
main.py                  # FastAPI routes and WebSocket endpoint
game/
  models.py              # Data models (GameState, PlayerState, …)
  logic.py               # Pure game logic (roles, votes, sabotage, elimination)
  manager.py             # In-memory game registry and WebSocket broadcast
templates/               # Jinja2 HTML templates
static/style.css         # Mobile-first ocean theme
activities.json          # Game content (activities, tasks, roles)
```
