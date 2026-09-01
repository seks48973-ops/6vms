"""Shared exception type for free-script fetchers (obscura.py, jnkie.py use
it). The luaprot fetch logic itself lives inline in bot.py."""


class FetchError(Exception):
    """Raised when nothing deliverable can be obtained from a fetcher."""
