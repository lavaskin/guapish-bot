from firebase_admin import credentials, firestore, initialize_app


class Firebase:
	def __init__(self):
		_dbCred = credentials.Certificate('./firebase.json')
		initialize_app(_dbCred)
		self.firestore = firestore.client()

	def collection(self, name: str) -> firestore.CollectionReference:
		return self.firestore.collection(name)
