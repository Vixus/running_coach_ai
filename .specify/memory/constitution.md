<!--
Sync Impact Report - Constitution v1.1.0
Version change: 1.0.0 → 1.1.0
Bump type: MINOR — material update to quantified performance standard

Modified sections:
  - Performance Standards: "30-minute polling intervals for Garmin activity sync" →
    "≤10-minute polling intervals for Garmin activity sync"
    Rationale: SC-002 requires activity feedback within 10 minutes of Garmin sync;
    30-minute intervals made that SLA impossible to meet.

Added sections: None
Removed sections: None

Templates requiring updates:
  ✅ tasks-template.md — "Tests are OPTIONAL" wording updated to align with Principle VII

Follow-up TODOs:
  - tasks.md for spec 001 header still reads "Tests: Not requested — no test tasks generated."
    This violates Principle VII. Owner: update header and verify T074 covers timestamp tests.
  - spec 001 tasks T045/T047 and contracts/slack-events.md still reference /admin instead of !admin.
    Owner: complete T068 and amend contract artifact.
-->

# AI Running Coach Constitution

## Core Principles

### I. Athlete Data Isolation (NON-NEGOTIABLE)

Every database query, API call, Claude context, and Slack message MUST be scoped to a specific `athlete_id`. No cross-athlete data leakage is acceptable. Use `scoped_query()` helper for all athlete-scoped lookups. Rationale: Privacy and security - each athlete's health data, conversations, and training plans must remain completely separate.

### II. AI Coach Persona Integrity

Claude integration MUST maintain the veteran running coach persona through structured system prompts. Responses use natural language only (no chatbot artifacts). Structured outputs via XML tags (`<plan>`, `<remember>`, `<garmin_sync/>`) are processed silently. Rationale: User experience - athletes interact with a trusted coach, not an AI system.

### III. Metric-First Architecture

Internal storage MUST use kilometers and min/km paces. Display layer converts to miles/min-mile for athletes. All `<plan>` JSON tags use internal metric format. Rationale: Consistency with Garmin API and mathematical precision - avoids floating-point accumulation errors from repeated imperial conversions.

### IV. Encrypted Secrets Management

Garmin credentials MUST be encrypted at rest using Fernet symmetric encryption. Encryption key generated per deployment, stored in environment. Loss of key requires credential re-entry. Rationale: Security - protects athlete Garmin accounts from database compromise.

### V. Graceful Degradation

System MUST continue operating when external APIs fail (Garmin, Weather, Claude). Log errors but don't crash. Skip failed operations and retry on next cycle. Rationale: Reliability - athletes expect consistent service even during API outages.

### VI. Structured Observability

All logging MUST use structured format with timestamps, levels, and component names. Configuration via `LOG_LEVEL` environment variable. Rationale: Debugging and monitoring - enables effective troubleshooting in production Docker deployment.

### VII. Test-First Development

All features MUST have corresponding unit tests. Integration tests for API interactions. Mock external services (Garmin, Claude, Slack). Rationale: Quality assurance - prevents regressions in complex athlete data flows and AI integrations.

### VIII. Configuration as Code

Application settings MUST load from environment variables via Pydantic validation. No hardcoded values. `.env` files for local development. Rationale: Deployment flexibility - supports multiple environments (local, Docker, Synology NAS) without code changes.

## Additional Constraints

### Security Requirements

- Invite-only access: `ALLOWED_SLACK_USER_IDS` seeds initial allowlist, per-athlete `allowed` flag for runtime management
- Admin controls: `ADMIN_SLACK_USER_ID` can manage access and perform maintenance operations
- No inbound ports: Slack Socket Mode only - outbound connections only
- Secrets isolation: API keys, tokens, and encryption keys in environment only

### Technology Stack Commitments

- Python 3.11+ with type hints
- SQLite + SQLAlchemy for data persistence
- Anthropic Claude API for AI coaching intelligence
- Slack Bolt for bot framework
- APScheduler for background jobs
- Docker Compose for deployment

### Performance Standards

- Sub-10-minute response times for Slack interactions
- ≤10-minute polling intervals for Garmin activity sync (SC-002: post-run feedback MUST be delivered within 10 minutes of activity sync during active hours 06:00–22:00)
- Daily health snapshots and weekly reviews
- Memory-efficient: Rolling 30-message conversation windows

## Development Workflow

### Speckit Methodology

All development MUST follow `specify → clarify → plan → tasks → implement` workflow using speckit commands. 95% confidence rule: No changes until requirements are fully understood. Rationale: Structured development - ensures thorough planning and prevents scope creep.

### Code Quality

- Linting: `ruff check` with auto-fix capability
- Idempotent schema: `Base.metadata.create_all()` for database setup
- Type hints: Full type annotation coverage
- Documentation: CLAUDE.md for development guidance

## Governance

Constitution supersedes all other practices. Amendments require:

1. Codebase analysis to verify compliance
2. Update to dependent templates (`.specify/templates/*`)
3. Version bump following semantic versioning
4. Documentation of rationale and impact

All pull requests must verify constitution compliance. Complexity must be justified against YAGNI principles. Use `CLAUDE.md` for runtime development guidance.

**Version**: 1.1.0 | **Ratified**: 2026-04-03 | **Last Amended**: 2026-04-04
