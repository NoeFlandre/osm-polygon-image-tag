class ImageTagPipelineError(Exception):
    """Base class for expected operator-facing failures."""


class ConfigurationError(ImageTagPipelineError):
    """Raised when configured paths violate the storage contract."""
