"""Base AI client interface."""


class BaseAIClient:
    """Base class for AI provider clients."""

    async def get_response(self, messages, **kwargs):
        raise NotImplementedError
