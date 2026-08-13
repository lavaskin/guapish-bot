from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping


@dataclass(slots=True)
class MovieRequestModel:
	document_id: str | None
	user_id: str
	user_name: str
	title: str
	year: int
	date: datetime
	picked: bool = False

	@classmethod
	def from_dict(cls, data: Mapping[str, Any], document_id: str | None = None) -> 'MovieRequestModel':
		return cls(
			document_id=document_id,
			user_id=str(data['user_id']),
			user_name=str(data['user_name']),
			title=str(data['title']),
			year=int(data['year']),
			date=data['date'],
			picked=bool(data.get('picked', False)),
		)

	@classmethod
	def from_snapshot(cls, snapshot) -> 'MovieRequestModel':
		return cls.from_dict(snapshot.to_dict(), document_id=snapshot.id)

	def to_dict(self) -> dict[str, Any]:
		return {
			'user_id': self.user_id,
			'user_name': self.user_name,
			'title': self.title,
			'year': self.year,
			'date': self.date,
			'picked': self.picked,
		}
