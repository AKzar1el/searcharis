"""Bound human-facing errors from untrusted local CI inputs."""


def human_input_error(error: Exception, *, limit: int = 240) -> str:
    """Keep diagnostic details without allowing control characters or log floods."""
    display = repr(str(error))[1:-1]
    return display if len(display) <= limit else display[:limit - 3] + "..."
