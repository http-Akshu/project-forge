import re
import unicodedata


def create_slug(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii")

    slug = re.sub(r"[^a-zA-Z0-9]+", "-", ascii_value)
    slug = slug.strip("-").lower()

    return slug or "untitled-project"