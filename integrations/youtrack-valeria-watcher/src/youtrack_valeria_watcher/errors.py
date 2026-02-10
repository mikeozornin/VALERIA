class WatcherError(RuntimeError):
    """Base error safe to show in service logs."""


class ConfigError(WatcherError):
    """Invalid or incomplete watcher configuration."""


class ExternalServiceError(WatcherError):
    """A remote service did not satisfy its contract."""
