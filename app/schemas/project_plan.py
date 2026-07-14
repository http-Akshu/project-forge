from pydantic import BaseModel, Field, field_validator

from app.core.state import ProjectSize, TaskType


class PlannedTask(BaseModel):
    position: int = Field(ge=1)
    title: str = Field(min_length=3, max_length=200)
    description: str = Field(min_length=10)
    task_type: TaskType
    acceptance_criteria: list[str] = Field(min_length=1)
    estimated_days: int = Field(default=1, ge=1, le=5)

    @field_validator("acceptance_criteria")
    @classmethod
    def validate_acceptance_criteria(cls, values: list[str]) -> list[str]:
        cleaned = [value.strip() for value in values if value.strip()]

        if not cleaned:
            raise ValueError("At least one acceptance criterion is required.")

        return cleaned


class ProjectPlan(BaseModel):
    title: str = Field(min_length=3, max_length=200)
    short_name: str = Field(min_length=2, max_length=80)
    problem_statement: str = Field(min_length=40)
    solution_summary: str = Field(min_length=40)
    target_users: list[str] = Field(min_length=1)
    project_size: ProjectSize
    estimated_duration_days: int = Field(ge=5, le=60)

    primary_features: list[str] = Field(min_length=3)
    excluded_features: list[str] = Field(default_factory=list)

    frontend_stack: list[str] = Field(default_factory=list)
    backend_stack: list[str] = Field(default_factory=list)
    database_stack: list[str] = Field(default_factory=list)
    testing_stack: list[str] = Field(default_factory=list)

    architecture_summary: str = Field(min_length=40)
    security_considerations: list[str] = Field(min_length=1)
    deployment_strategy: str = Field(min_length=20)

    tasks: list[PlannedTask] = Field(min_length=5)

    @field_validator(
        "target_users",
        "primary_features",
        "frontend_stack",
        "backend_stack",
        "database_stack",
        "testing_stack",
        "security_considerations",
        "excluded_features",
    )
    @classmethod
    def clean_string_lists(cls, values: list[str]) -> list[str]:
        return [value.strip() for value in values if value.strip()]

    @field_validator("tasks")
    @classmethod
    def validate_task_positions(
        cls,
        tasks: list[PlannedTask],
    ) -> list[PlannedTask]:
        positions = [task.position for task in tasks]

        if len(positions) != len(set(positions)):
            raise ValueError("Task positions must be unique.")

        expected_positions = list(range(1, len(tasks) + 1))

        if sorted(positions) != expected_positions:
            raise ValueError(
                "Task positions must begin at 1 and remain sequential."
            )

        return sorted(tasks, key=lambda task: task.position)