# running_coach_ai Development Guidelines

Auto-generated from all feature plans. Last updated: 2026-04-06

## Active Technologies
- Python 3.11 + `timezonefinder>=6.0.0` (new), `APScheduler 3.x` (existing), `SQLAlchemy` (existing), `garminconnect`/`garth` (existing), `slack-bolt` (existing) (002-feature-athlete-timezone)
- SQLite via SQLAlchemy ORM — existing `athletes.timezone TEXT NULLABLE` column (002-feature-athlete-timezone)
- Python 3.11 + Slack Bolt for Python (SocketMode), SQLAlchemy 2.x, Anthropic Claude API, Alembic (migrations) (003-garmin-creds-modal)
- SQLite via SQLAlchemy — two new nullable columns on `Athlete` (003-garmin-creds-modal)
- Python 3.11+ + SQLAlchemy 2.x, Anthropic Claude API, Slack Bolt, APScheduler, Alembic (004-multi-coach-personas)
- SQLite via SQLAlchemy � one new column: `Athlete.coach_key TEXT` (004-multi-coach-personas)
- SQLite via SQLAlchemy — one new column: `Athlete.coach_key TEXT` (004-multi-coach-personas)

- Python 3.11+ + `timezonefinder>=6.0.0` (new), `apscheduler>=3.10.0,<4.0.0`, `sqlalchemy>=2.0.0` (002-feature-athlete-timezone)

## Project Structure

```text
src/
tests/
```

## Commands

cd src; pytest; ruff check .

## Code Style

Python 3.11+: Follow standard conventions

## Recent Changes
- 004-multi-coach-personas: Added Python 3.11+ + SQLAlchemy 2.x, Anthropic Claude API, Slack Bolt, APScheduler, Alembic
- 004-multi-coach-personas: Added Python 3.11+ + SQLAlchemy 2.x, Anthropic Claude API, Slack Bolt, APScheduler, Alembic
- 003-garmin-creds-modal: Added Python 3.11 + Slack Bolt for Python (SocketMode), SQLAlchemy 2.x, Anthropic Claude API, Alembic (migrations)


<!-- MANUAL ADDITIONS START -->
<!-- MANUAL ADDITIONS END -->
