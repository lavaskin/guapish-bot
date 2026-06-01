from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(slots=True)
class MetadataModel:
	document_id: str | None
	last_id: str | None = None

	@classmethod
	def from_dict(cls, data: Mapping[str, Any], document_id: str | None = None) -> 'MetadataModel':
		return cls(
			document_id=document_id,
			last_id=str(data['last_id']) if data.get('last_id') is not None else None,
		)

	@classmethod
	def from_snapshot(cls, snapshot) -> 'MetadataModel':
		return cls.from_dict(snapshot.to_dict(), document_id=snapshot.id)

	def to_dict(self) -> dict[str, Any]:
		return {
			'last_id': self.last_id,
		}
