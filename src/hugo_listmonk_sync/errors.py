"""Application-specific exceptions."""


class SyncError(Exception):
    """Base class for expected synchronization errors."""


class ConfigError(SyncError):
    """Raised when startup configuration is invalid."""


class FeedError(SyncError):
    """Raised when the feed cannot be fetched or validated."""


class ListmonkError(SyncError):
    """Raised when a Listmonk operation fails or returns invalid data."""
