class ImageTagPipelineError(Exception):
    """Base class for expected operator-facing failures."""


class ConfigurationError(ImageTagPipelineError):
    """Raised when configured paths violate the storage contract."""


class PreflightError(ImageTagPipelineError):
    """Raised when a read-only environment check fails."""


class PublicationError(ImageTagPipelineError):
    """Raised when publication cannot be proven safe and complete."""
