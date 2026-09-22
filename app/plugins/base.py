from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, asdict
from pathlib import Path


@dataclass
class ArchiveEntry:
    name: str
    offset: int
    size: int
    packed_size: int | None = None
    compression: str | None = None
    file_id: int | None = None
    extra: int | None = None

    def to_dict(self):
        return asdict(self)


class FormatPlugin(ABC):
    name = 'Unnamed plugin'

    @abstractmethod
    def probe(self, path: Path, head: bytes) -> int:
        """Return 0-100 confidence that this plugin understands the file."""
        raise NotImplementedError

    @abstractmethod
    def list_entries(self, path: Path) -> list[ArchiveEntry]:
        raise NotImplementedError

    @abstractmethod
    def extract_entry(self, path: Path, entry: ArchiveEntry, output_path: Path) -> None:
        raise NotImplementedError
