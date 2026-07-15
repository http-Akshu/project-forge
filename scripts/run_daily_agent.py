from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from app.agents.orchestrator import (
    OrchestratorError,
    TaskOrchestrator,
)
from app.database.init_db import initialize_database
from app.database.session import SessionLocal
from app.services.run_lock import RunLock, RunLockError


console = Console()


def main() -> None:
    initialize_database()

    try:
        with RunLock():
            db = SessionLocal()

            try:
                console.print(
                    Panel.fit(
                        "Selecting and implementing one project task...",
                        title="ProjectForge Daily Run",
                    )
                )

                orchestrator = TaskOrchestrator()
                result = orchestrator.run_one_task(db=db)

                if result.developer_result:
                    development = result.developer_result

                    details = Table(title="Daily Run Result")
                    details.add_column("Property", style="bold")
                    details.add_column("Value")

                    details.add_row(
                        "Project",
                        result.project.title,
                    )

                    details.add_row(
                        "Task",
                        (
                            f"{development.task.position}. "
                            f"{development.task.title}"
                        ),
                    )

                    details.add_row(
                        "Commit",
                        development.commit_hash[:12],
                    )

                    details.add_row(
                        "Message",
                        development.plan.commit_message,
                    )

                    details.add_row(
                        "Project status",
                        result.project.status,
                    )

                    console.print(details)

                console.print(
                    Panel.fit(
                        result.message,
                        title="Daily Run Complete",
                    )
                )

            finally:
                db.close()

    except (RunLockError, OrchestratorError) as exc:
        console.print(
            Panel.fit(
                str(exc),
                title="Daily Run Failed",
                border_style="red",
            )
        )

        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()