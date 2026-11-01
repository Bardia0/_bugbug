from abc import ABC, abstractmethod


class DiscoveryTool(ABC):
    """Abstract base for any subdomain-discovery backend."""
    name: str = "base"
    description: str = ""

    @abstractmethod
    def is_available(self) -> bool:
        """True if the tool's binary/dependency is installed."""

    @abstractmethod
    def discover(self, domain: str, timeout: int = 300) -> set[str]:
        """Return a set of discovered hostnames for `domain`.
        Must be safe to call repeatedly; may raise on failure.
        """