from enum import StrEnum


class ProjectStatus(StrEnum):
    IDEA_RESEARCH = "idea_research"
    PLANNING = "planning"
    APPROVED = "approved"
    SCAFFOLDING = "scaffolding"
    IMPLEMENTING = "implementing"
    TESTING = "testing"
    REVIEWING = "reviewing"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    ARCHIVED = "archived"


class TaskStatus(StrEnum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    TESTING = "testing"
    FAILED = "failed"
    BLOCKED = "blocked"
    COMPLETED = "completed"


class TaskType(StrEnum):
    RESEARCH = "research"
    PLANNING = "planning"
    SETUP = "setup"
    FEATURE = "feature"
    BUG_FIX = "bug_fix"
    TESTING = "testing"
    DOCUMENTATION = "documentation"
    DEPLOYMENT = "deployment"


class ProjectSize(StrEnum):
    SMALL = "small"
    MEDIUM = "medium"