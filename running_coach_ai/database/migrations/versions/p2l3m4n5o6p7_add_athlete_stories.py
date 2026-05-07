"""add_athlete_stories

Revision ID: p2l3m4n5o6p7
Revises: o1k2l3m4n5o6
Create Date: 2026-05-07
"""

from alembic import op
import sqlalchemy as sa


revision = "p2l3m4n5o6p7"
down_revision = "o1k2l3m4n5o6"
branch_labels = None
depends_on = None


def upgrade():
    # 1. Extend athletes table
    with op.batch_alter_table("athletes") as batch:
        batch.add_column(sa.Column("story_opt_in", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch.add_column(sa.Column("story_intro_seen_at", sa.DateTime(), nullable=True))

    # 2. Create athlete_stories
    op.create_table(
        "athlete_stories",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("athlete_id", sa.Integer(), sa.ForeignKey("athletes.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("milestone_type", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("editorial_body", sa.Text(), nullable=False),
        sa.Column("cover_image_path", sa.Text(), nullable=True),
        sa.Column("template_key", sa.Text(), nullable=False, server_default="vogue"),
        sa.Column("template_locked_by_athlete", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("share_token", sa.Text(), unique=True, nullable=False, index=True),
        sa.Column("regeneration_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_regenerated_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.current_timestamp()),
        sa.Column("published_at", sa.DateTime(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(), nullable=True, index=True),
        sa.CheckConstraint("milestone_type = 'race_complete'",
                           name="ck_athlete_stories_milestone_type"),
    )
    op.create_index("ix_athlete_stories_athlete_deleted",
                    "athlete_stories", ["athlete_id", "deleted_at"])
    op.create_index("ix_athlete_stories_athlete_milestone",
                    "athlete_stories", ["athlete_id", "milestone_type", "deleted_at"])

    # 3. Create story_interview_sessions
    op.create_table(
        "story_interview_sessions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("athlete_id", sa.Integer(), sa.ForeignKey("athletes.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("trigger_kind", sa.Text(), nullable=False),
        sa.Column("trigger_context_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("started_at", sa.DateTime(), nullable=False, server_default=sa.func.current_timestamp()),
        sa.Column("completed_at", sa.DateTime(), nullable=True, index=True),
        sa.Column("skipped", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("story_id", sa.Integer(), sa.ForeignKey("athlete_stories.id"), nullable=True),
        sa.CheckConstraint(
            "trigger_kind IN ('pr_set','race_upcoming','race_complete',"
            "'pace_recalibration','difficult_week')",
            name="ck_story_sessions_trigger_kind",
        ),
    )
    op.create_index("ix_story_sessions_athlete_completed",
                    "story_interview_sessions", ["athlete_id", "completed_at"])
    op.create_index("ix_story_sessions_athlete_story",
                    "story_interview_sessions", ["athlete_id", "story_id"])

    # 4. Create story_questions
    op.create_table(
        "story_questions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("session_id", sa.Integer(),
                  sa.ForeignKey("story_interview_sessions.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("athlete_id", sa.Integer(), sa.ForeignKey("athletes.id"), nullable=False, index=True),
        sa.Column("story_id", sa.Integer(), sa.ForeignKey("athlete_stories.id"), nullable=True),
        sa.Column("question_index", sa.Integer(), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("options_json", sa.JSON(), nullable=False),
        sa.Column("answer", sa.Text(), nullable=True),
        sa.Column("is_custom_answer", sa.Boolean(), nullable=True),
        sa.Column("asked_at", sa.DateTime(), nullable=False, server_default=sa.func.current_timestamp()),
        sa.Column("answered_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("session_id", "question_index", name="uq_story_question_index"),
    )
    op.create_index("ix_story_questions_athlete_answered",
                    "story_questions", ["athlete_id", "answered_at"])

    # 5. Create story_images
    op.create_table(
        "story_images",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("story_id", sa.Integer(),
                  sa.ForeignKey("athlete_stories.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("filename", sa.Text(), nullable=False),
        sa.Column("caption", sa.Text(), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("uploaded_at", sa.DateTime(), nullable=False, server_default=sa.func.current_timestamp()),
    )
    op.create_index("ix_story_images_story_sort",
                    "story_images", ["story_id", "sort_order"])


def downgrade():
    op.drop_index("ix_story_images_story_sort", "story_images")
    op.drop_table("story_images")
    op.drop_index("ix_story_questions_athlete_answered", "story_questions")
    op.drop_table("story_questions")
    op.drop_index("ix_story_sessions_athlete_story", "story_interview_sessions")
    op.drop_index("ix_story_sessions_athlete_completed", "story_interview_sessions")
    op.drop_table("story_interview_sessions")
    op.drop_index("ix_athlete_stories_athlete_milestone", "athlete_stories")
    op.drop_index("ix_athlete_stories_athlete_deleted", "athlete_stories")
    op.drop_table("athlete_stories")
    with op.batch_alter_table("athletes") as batch:
        batch.drop_column("story_intro_seen_at")
        batch.drop_column("story_opt_in")
