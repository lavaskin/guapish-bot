from src.core.config import AppConfig
from src.core.firebase import Firebase
from src.features.movies.metadata import MetadataModel
from src.features.movies.movie_request import MovieRequestModel

from firebase_admin import firestore
from google.cloud.firestore_v1.base_query import FieldFilter


class MoviesStore:
	def __init__(self, firebase: Firebase, config: AppConfig):
		self.firebase = firebase
		self.requests_collection = config.require_env('REQUESTS_COLLECTION')
		self.metadata_collection = config.require_env('METADATA_COLLECTION')

	def get_requests_ref(self) -> firestore.CollectionReference:
		return self.firebase.collection(self.requests_collection)

	def get_metadata_doc(self) -> firestore.DocumentReference:
		return self.firebase.collection(self.metadata_collection).document('meta')

	def get_metadata(self) -> MetadataModel:
		doc = self.get_metadata_doc().get()
		return MetadataModel.from_snapshot(doc) if doc.exists else MetadataModel(document_id='meta')

	def get_user_requests(self, user_id: str) -> list[MovieRequestModel]:
		docs = self.get_requests_ref().where(filter=FieldFilter('user_id', '==', user_id)).stream()
		return [MovieRequestModel.from_snapshot(doc) for doc in docs]

	def get_all_requests(self, sort_direction: str = 'desc') -> list[MovieRequestModel]:
		docs = self._unpicked_query().stream()
		return self._sort_by_date(
			[MovieRequestModel.from_snapshot(doc) for doc in docs],
			sort_direction,
		)

	def get_eligible_requests(self, sort_direction: str = 'desc', metadata: MetadataModel | None = None) -> list[MovieRequestModel]:
		query = self._unpicked_query()
		last_id = (metadata or self.get_metadata()).last_id
		if last_id:
			query = query.where(filter=FieldFilter('user_id', '!=', last_id))
		docs = query.stream()
		return self._sort_by_date(
			[MovieRequestModel.from_snapshot(doc) for doc in docs],
			sort_direction,
		)

	def add_request(self, request: MovieRequestModel) -> None:
		self.get_requests_ref().add(request.to_dict())

	def mark_picked(self, request: MovieRequestModel) -> None:
		self._write_pick(request, picked=True, last_id=request.user_id)

	def unmark_picked(self, request: MovieRequestModel, previous_last_id: str | None) -> None:
		"""Undo mark_picked, restoring the last winner it replaced."""
		self._write_pick(request, picked=False, last_id=previous_last_id)

	def _write_pick(self, request: MovieRequestModel, *, picked: bool, last_id: str | None) -> None:
		if not request.document_id:
			raise ValueError('Cannot change the picked state of a request without a document_id')

		# One batch, so the request and the last winner can never disagree.
		batch = self.firebase.batch()
		batch.update(self.get_requests_ref().document(request.document_id), {'picked': picked})
		batch.set(self.get_metadata_doc(), {'last_id': last_id}, merge=True)
		batch.commit()

	def _unpicked_query(self):
		return self.get_requests_ref().where(filter=FieldFilter('picked', '==', False))

	def _sort_by_date(self, requests: list[MovieRequestModel], sort_direction: str) -> list[MovieRequestModel]:
		reverse = sort_direction.lower() != 'asc'
		return sorted(requests, key=lambda request: request.date, reverse=reverse)
