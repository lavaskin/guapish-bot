from src.models.app_config import AppConfig
from src.models.metadata_model import MetadataModel

from firebase_admin import credentials, firestore, initialize_app


class FirebaseConfig:
	def __init__(self, app_config: AppConfig):
		self.requests_collection = app_config.requests_collection
		self.metadata_collection = app_config.metadata_collection
		self.patreon_role = app_config.patreon_role

		_dbCred = credentials.Certificate('./firebase.json')
		initialize_app(_dbCred)
		self.firestore = firestore.client()

	def get_requests_ref(self) -> firestore.CollectionReference:
		return self.firestore.collection(self.requests_collection)

	def get_metadata_doc(self) -> firestore.DocumentReference:
		return self.firestore.collection(self.metadata_collection).document('meta')
	
	def get_metadata(self) -> MetadataModel:
		doc = self.get_metadata_doc().get()
		return MetadataModel.from_snapshot(doc) if doc.exists else MetadataModel(document_id='meta')
