from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.agents.planner import PlannerAgent, PlannerAgentError
from app.database.session import Base
from app.schemas.project_plan import ProjectPlan
from app.services.knowledge_vault import KnowledgeVaultService


class FakeDeepSeekService:
    def generate_structured(
        self,
        *,
        schema,
        system_prompt: str,
        user_prompt: str,
        model: str | None = None,
    ) -> ProjectPlan:
        return schema.model_validate(
            {
                "title": "Neighborhood Tool Library",
                "short_name": "Tool Library",
                "problem_statement": (
                    "Neighbors often purchase tools they use only once, "
                    "creating unnecessary expense and waste."
                ),
                "solution_summary": (
                    "A small web application will let neighbors catalog, "
                    "request, lend, and return shared household tools."
                ),
                "target_users": [
                    "Neighborhood residents",
                    "Community organizers",
                ],
                "project_size": "small",
                "estimated_duration_days": 8,
                "primary_features": [
                    "Tool catalog",
                    "Borrowing requests",
                    "Return tracking",
                ],
                "excluded_features": [
                    "Online payments",
                ],
                "frontend_stack": [
                    "Next.js",
                    "TypeScript",
                    "Tailwind CSS",
                ],
                "backend_stack": [
                    "Next.js server actions",
                    "Zod",
                ],
                "database_stack": [
                    "SQLite",
                    "Drizzle ORM",
                ],
                "testing_stack": [
                    "Vitest",
                    "Playwright",
                ],
                "architecture_summary": (
                    "The application uses a Next.js frontend and server-side "
                    "actions connected to a relational SQLite database."
                ),
                "security_considerations": [
                    "Validate all server-side input",
                    "Restrict update operations to resource owners",
                ],
                "deployment_strategy": (
                    "Deploy the web application to Vercel and use a hosted "
                    "PostgreSQL database when production persistence is needed."
                ),
                "tasks": [
                    {
                        "position": position,
                        "title": f"Task {position}",
                        "description": (
                            f"Implement the complete requirements for task "
                            f"number {position}."
                        ),
                        "task_type": (
                            "planning" if position == 1 else "feature"
                        ),
                        "acceptance_criteria": [
                            f"Task {position} requirements are complete"
                        ],
                        "estimated_days": 1,
                    }
                    for position in range(1, 9)
                ],
            }
        )


@pytest.fixture
def db_session() -> Session:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )

    Base.metadata.create_all(engine)

    with Session(engine) as session:
        yield session


def test_planner_creates_project_and_tasks(
    db_session: Session,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    generated_directory = tmp_path / "generated-projects"
    vault_directory = tmp_path / "knowledge-vault"

    monkeypatch.setattr(
        "app.agents.planner.get_settings",
        lambda: type(
            "Settings",
            (),
            {
                "generated_projects_dir": generated_directory,
                "knowledge_vault_dir": vault_directory,
                "deepseek_reasoning_model": "fake-model",
            },
        )(),
    )

    knowledge_service = KnowledgeVaultService()
    knowledge_service.settings.knowledge_vault_dir = vault_directory

    planner = PlannerAgent(
        deepseek_service=FakeDeepSeekService(),
        knowledge_vault_service=knowledge_service,
    )

    result = planner.create_project(
        db=db_session,
        problem=(
            "People in a neighborhood need a better way to share household "
            "tools that are rarely used."
        ),
        preferred_project_size="small",
    )

    assert result.project.id is not None
    assert result.project.slug == "neighborhood-tool-library"
    assert len(result.project.tasks) == 8
    assert result.project_directory.exists()
    assert result.knowledge_vault_directory.exists()

    assert (
        result.knowledge_vault_directory / "PROJECT_EXPLANATION.md"
    ).exists()

    assert (
        result.knowledge_vault_directory / "ARCHITECTURE.md"
    ).exists()

    assert (
        result.project_directory / ".projectforge.json"
    ).exists()


def test_planner_rejects_second_active_project(
    db_session: Session,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    generated_directory = tmp_path / "generated-projects"
    vault_directory = tmp_path / "knowledge-vault"

    monkeypatch.setattr(
        "app.agents.planner.get_settings",
        lambda: type(
            "Settings",
            (),
            {
                "generated_projects_dir": generated_directory,
                "knowledge_vault_dir": vault_directory,
                "deepseek_reasoning_model": "fake-model",
            },
        )(),
    )

    knowledge_service = KnowledgeVaultService()
    knowledge_service.settings.knowledge_vault_dir = vault_directory

    planner = PlannerAgent(
        deepseek_service=FakeDeepSeekService(),
        knowledge_vault_service=knowledge_service,
    )

    problem = (
        "People in a neighborhood need a better way to share household "
        "tools that are rarely used."
    )

    planner.create_project(
        db=db_session,
        problem=problem,
        preferred_project_size="small",
    )

    with pytest.raises(
        PlannerAgentError,
        match="An active project already exists",
    ):
        planner.create_project(
            db=db_session,
            problem=problem,
            preferred_project_size="small",
        )