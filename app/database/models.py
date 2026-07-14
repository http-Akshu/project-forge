from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.state import ProjectSize, ProjectStatus, TaskStatus, TaskType
from app.database.session import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(primary_key=True)

    name: Mapped[str] = mapped_column(
        String(120),
        unique=True,
        nullable=False,
        index=True,
    )

    slug: Mapped[str] = mapped_column(
        String(140),
        unique=True,
        nullable=False,
        index=True,
    )

    title: Mapped[str] = mapped_column(String(200), nullable=False)

    problem_statement: Mapped[str] = mapped_column(Text, nullable=False)

    solution_summary: Mapped[str] = mapped_column(Text, nullable=False)

    target_users: Mapped[str] = mapped_column(Text, nullable=False)

    project_size: Mapped[str] = mapped_column(
        String(30),
        default=ProjectSize.SMALL.value,
        nullable=False,
    )

    status: Mapped[str] = mapped_column(
        String(30),
        default=ProjectStatus.PLANNING.value,
        nullable=False,
        index=True,
    )

    technology_stack: Mapped[str] = mapped_column(Text, nullable=False)

    local_path: Mapped[Optional[str]] = mapped_column(
        String(500),
        nullable=True,
    )

    knowledge_vault_path: Mapped[Optional[str]] = mapped_column(
        String(500),
        nullable=True,
    )

    github_repository_url: Mapped[Optional[str]] = mapped_column(
        String(500),
        nullable=True,
    )

    current_phase: Mapped[str] = mapped_column(
        String(100),
        default="Planning",
        nullable=False,
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
        index=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )

    tasks: Mapped[list["ProjectTask"]] = relationship(
        back_populates="project",
        cascade="all, delete-orphan",
        order_by="ProjectTask.position",
    )

    model_calls: Mapped[list["ModelCall"]] = relationship(
        back_populates="project",
        cascade="all, delete-orphan",
    )


class ProjectTask(Base):
    __tablename__ = "project_tasks"

    id: Mapped[int] = mapped_column(primary_key=True)

    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    position: Mapped[int] = mapped_column(Integer, nullable=False)

    title: Mapped[str] = mapped_column(String(200), nullable=False)

    description: Mapped[str] = mapped_column(Text, nullable=False)

    task_type: Mapped[str] = mapped_column(
        String(30),
        default=TaskType.FEATURE.value,
        nullable=False,
    )

    status: Mapped[str] = mapped_column(
        String(30),
        default=TaskStatus.PENDING.value,
        nullable=False,
        index=True,
    )

    acceptance_criteria: Mapped[str] = mapped_column(Text, nullable=False)

    estimated_days: Mapped[int] = mapped_column(
        Integer,
        default=1,
        nullable=False,
    )

    repair_attempts: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )

    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )

    project: Mapped["Project"] = relationship(back_populates="tasks")


class ModelCall(Base):
    __tablename__ = "model_calls"

    id: Mapped[int] = mapped_column(primary_key=True)

    project_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    agent_name: Mapped[str] = mapped_column(String(100), nullable=False)

    model_name: Mapped[str] = mapped_column(String(100), nullable=False)

    input_tokens: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )

    output_tokens: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )

    estimated_cost_usd: Mapped[float] = mapped_column(
        Float,
        default=0,
        nullable=False,
    )

    successful: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )

    error_message: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
        index=True,
    )

    project: Mapped[Optional["Project"]] = relationship(
        back_populates="model_calls"
    )