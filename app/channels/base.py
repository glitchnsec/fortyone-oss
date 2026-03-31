"""
Channel abstraction — everything the pipeline needs to talk to a user.

Each channel knows how to:
  • send a message to a user address
  • report its own name (used to route results back from workers)

Adding a new channel = subclass Channel, implement send(), register
in main.py.  The pipeline and workers are channel-agnostic.
"""
from abc import ABC, abstractmethod


class Channel(ABC):
    #: Short lowercase identifier, e.g. "sms", "slack", "telegram"
    name: str

    #: Sent to the user on unhandled pipeline errors
    error_reply: str = "Something went wrong on my end — try again in a moment."

    @abstractmethod
    async def send(self, to: str, body: str) -> bool:
        """
        Deliver `body` to the user identified by `to` (address on this channel).
        Returns True on success, False on failure.
        """
        ...

    def __repr__(self) -> str:
        return f"<Channel name={self.name!r}>"
