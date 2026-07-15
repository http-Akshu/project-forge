import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path


class RunLockError(RuntimeError):
    """Raised when another ProjectForge run is already active."""


class RunLock:
    def __init__(
        self,
        *,
        lock_path: Path = Path(".projectforge-run.lock"),
        stale_after_hours: int = 6,
    ) -> None:
        self.lock_path = lock_path.resolve()
        self.stale_after = timedelta(hours=stale_after_hours)
        self.acquired = False

    def acquire(self) -> None:
        if self.lock_path.exists():
            if not self._is_stale():
                raise RunLockError(
                    "Another ProjectForge run appears to be active."
                )

            self.lock_path.unlink(missing_ok=True)

        payload = {
            "pid": os.getpid(),
            "started_at": datetime.now(timezone.utc).isoformat(),
        }

        self.lock_path.write_text(
            json.dumps(payload, indent=2) + "\n",
            encoding="utf-8",
        )

        self.acquired = True

    def release(self) -> None:
        if self.acquired:
            self.lock_path.unlink(missing_ok=True)
            self.acquired = False

    def _is_stale(self) -> bool:
        try:
            payload = json.loads(
                self.lock_path.read_text(encoding="utf-8")
            )

            started_at = datetime.fromisoformat(
                payload["started_at"]
            )

            if started_at.tzinfo is None:
                started_at = started_at.replace(
                    tzinfo=timezone.utc
                )

            age = datetime.now(timezone.utc) - started_at

            return age > self.stale_after

        except (
            OSError,
            KeyError,
            ValueError,
            json.JSONDecodeError,
        ):
            return True

    def __enter__(self) -> "RunLock":
        self.acquire()
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.release()