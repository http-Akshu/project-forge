from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from app.agents.developer import (
    DeveloperAgent,
    DeveloperAgentError,
)
from app.database.init_db import initialize_database
from app.database.session import SessionLocal


console = Console()


def main() -> None:
    initialize_database()
    db = SessionLocal()

    try:
        console.print(
            Panel.fit(
                "Loading the current task and generating code changes...",
                title="ProjectForge Developer",
            )
        )

        developer = DeveloperAgent()
        result = developer.run_current_task(db=db)

        file_table = Table(title="Applied File Changes")
        file_table.add_column("Operation")
        file_table.add_column("File")

        for change in result.changed_files:
            file_table.add_row(
                change.operation.value,
                str(change.path),
            )

        console.print(file_table)

        validation_table = Table(title="Validation Commands")
        validation_table.add_column("#", justify="right")
        validation_table.add_column("Command")

        for index, command in enumerate(
            result.validation_commands,
            start=1,
        ):
            validation_table.add_row(
                str(index),
                " ".join(command),
            )

        console.print(validation_table)

        console.print(
            Panel.fit(
                "\n".join(
                    [
                        (
                            f"Completed task: "
                            f"{result.task.position}. "
                            f"{result.task.title}"
                        ),
                        (
                            f"Commit: "
                            f"{result.commit_hash[:12]}"
                        ),
                        (
                            f"Message: "
                            f"{result.plan.commit_message}"
                        ),
                    ]
                ),
                title="Development Task Completed",
            )
        )

    except DeveloperAgentError as exc:
        console.print(
            Panel.fit(
                str(exc),
                title="Development Task Failed",
                border_style="red",
            )
        )

        raise SystemExit(1) from exc

    finally:
        db.close()


if __name__ == "__main__":
    main()