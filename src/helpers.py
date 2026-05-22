from datetime import datetime

from src.models.app_config import AppConfig
from src.models.firebase_config import FirebaseConfig
from src.models.guapish_bot import GuapishBot

from google.cloud.firestore_v1.base_query import FieldFilter


def create_bot():
	config = AppConfig()
	firebase_config = FirebaseConfig(config)
	return GuapishBot(config, firebase_config)

def get_months_since(date) -> int:
	# Get the difference in months between two dates
	now = datetime.now()
	return (now.year - date.year) * 12 + (now.month - date.month)

def get_request_entries(request) -> int:
	months = get_months_since(request['date'])
	if months >= 12:
		months += ((months - 12) * 2)
	return months + 1 # +1 for the default entry

def get_all_requests(ref):
	# Get all requests that are not picked
	raw_requests = ref.where(filter=FieldFilter('picked', '==', False)).stream()
	requests = [doc.to_dict() for doc in raw_requests]

	# Sort the movies by date requested and return them
	sorted_requests = sorted(requests, key=lambda x: x['date'], reverse=True)
	return sorted_requests
