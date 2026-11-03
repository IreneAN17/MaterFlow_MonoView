from abc import ABC, abstractmethod


class VisionBackend(ABC):
    @abstractmethod
    def infer_images(self, images, prompt, *, response_schema=None):
        """Independent image requests. One raw response per image."""

    @abstractmethod
    def infer_clip(self, frames, timestamps, prompt):
        """One ordered multi-image request; never independent image batching."""
