"""Provider abstraction: AbstractProvider ABC + CapabilityManifest dataclass."""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List


@dataclass
class CapabilityManifest:
    provider: str
    can_read_email: bool = False
    can_send_email: bool = False
    can_read_calendar: bool = False
    can_write_calendar: bool = False


class AbstractProvider(ABC):
    name: str
    auth_url: str
    token_url: str
    scopes: List[str]

    @abstractmethod
    def capability_manifest(self, granted_scopes: List[str]) -> CapabilityManifest:
        """Return the capability manifest for the given granted scopes."""
        ...
