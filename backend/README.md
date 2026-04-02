# Marriage Calculator

A points calculator for the Nepali card game **Marriage**.

## Project Structure

```
marriage_calculator/
├── backend/
│   ├── app.py          # Flask entry point
│   ├── routes.py       # REST API endpoints
│   ├── game_model.py   # Game/player CRUD
│   ├── hand_model.py   # Hand scoring logic + CRUD
│   ├── database.py     # DB connection + schema init
│   └── requirements.txt
└── frontend/
    └── index.html      # Single-page app (no build step)
```

## Prerequisites

- Python 3.11+
- PostgreSQL running locally (or remotely)

## Setup

### 1. Create the database

```sql
CREATE DATABASE marriage_calculator;
```

### 2. Install Python dependencies

```bash
cd backend
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 3. Configure environment variables

Set these before running (or create a `.env` file and use `python-dotenv`):

| Variable      | Default             | Description              |
|---------------|---------------------|--------------------------|
| `DB_HOST`     | `localhost`         | Postgres host            |
| `DB_PORT`     | `5432`              | Postgres port            |
| `DB_NAME`     | `marriage_calculator` | Database name           |
| `DB_USER`     | `postgres`          | Postgres user            |
| `DB_PASSWORD` | *(empty)*           | Postgres password        |
| `PORT`        | `5000`              | Flask port               |

Example (bash):
```bash
export DB_PASSWORD=mysecret
```

### 4. Run the app

```bash
cd backend
python app.py
```

The schema is created automatically on first startup.

Open **http://localhost:5000** in your browser.

---

## Scoring Formula

For each **non-winner** player:

```
points = -1 × (total_maal + penalty − maal_i × num_players)
```

Where `penalty` is:
- `seen`   → 3
- `unseen` → 10
- `duplee` → 0

The **winner's** points equal `-1 × sum(all non-winner points)`.

- Winner defaults to **Seen** when selected, but can be switched to **Duplee**
- Winner **cannot** be set to **Unseen**
- Multiple players (including the winner) can be **Duplee** in the same hand

---

## API Endpoints

| Method | Path                          | Description                    |
|--------|-------------------------------|--------------------------------|
| GET    | `/api/games`                  | List all games                 |
| POST   | `/api/games`                  | Create a game                  |
| GET    | `/api/games/:id`              | Get a game + players           |
| GET    | `/api/games/:id/scoreboard`   | Full scoreboard with hand data |
| POST   | `/api/games/:id/hands`        | Finalize a hand                |
| GET    | `/api/hands/:id`              | Get a single hand              |
