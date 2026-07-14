from app.database import models  # noqa: F401
from app.database.session import Base, engine


def initialize_database() -> None:
    Base.metadata.create_all(bind=engine)


if __name__ == "__main__":
    initialize_database()
    print("ProjectForge database initialized successfully.")