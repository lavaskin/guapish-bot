from dataclasses import dataclass


@dataclass(slots=True)
class Track:
	title: str
	webpage_url: str
	duration: int | None
	requester_id: int
	requester_name: str
	query: str
	thumbnail: str | None = None
	uploader: str | None = None
