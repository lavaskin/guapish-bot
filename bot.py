from datetime import datetime
import random
import discord
import os
from dotenv import load_dotenv
from firebase_admin import credentials, firestore, initialize_app
from google.cloud.firestore_v1.base_query import FieldFilter


ALLOWED_ROLLERS = ['148907812670406656', '373724550350897154', '289947773183197185']

# Load in env variables
load_dotenv()
_env = os.getenv('DEV_MODE', 'True')
_dev = True if _env == 'True' else False
PATREON_ROLE = os.getenv('ROLE_DEV') if _dev else os.getenv('ROLE_PROD')
print(f'LOG > Dev Mode={str(_dev)}')

# Firebase Setup
_dbCred = credentials.Certificate('./firebase.json')
COLLECTION = os.getenv('COLLECTION_DEV') if _dev else os.getenv('COLLECTION_PROD')
METADATA = os.getenv('META_DEV') if _dev else os.getenv('META_PROD')
initialize_app(_dbCred)
DB = firestore.client()


###################
# Helper Functions
def getRef() -> firestore.CollectionReference:
	return DB.collection(COLLECTION)

def getMeta() -> firestore.DocumentReference:
	# Get the metadata document (the id is always 'meta')
	return DB.collection(METADATA).document('meta')

def getMonthsSince(date) -> int:
	# Get the difference in months between two dates
	now = datetime.now()
	return (now.year - date.year) * 12 + (now.month - date.month)
	

############
# Bot Setup
bot = discord.Bot()

@bot.event
async def on_application_command_error(ctx):
	print(f' ERR > \n{ctx}')

@bot.event
async def on_ready():
    print('LOG > Bot is ready!')
    

###########
# Commands
@bot.slash_command(description='Request a given movie each month. Requests reset at the start of each month (PST).')
async def request(ctx, title: str, year: int):
	user = str(ctx.author.id)
	ref  = getRef()
	now = datetime.now()

	# Check if the user has the Patreon role
	roles = [str(role.id) for role in ctx.author.roles]
	if PATREON_ROLE not in roles:
		await ctx.respond('You must be a Patreon sub to use this command! Subscribe here:\n\t*https://www.patreon.com/GUAPISH*')
		return

	# Check if user already has a request by checking if any of the documents have the same user id in the 'user' field
	existingReqs = [doc.to_dict() for doc in ref.where(filter=FieldFilter('user_id', '==', user)).stream()]
	for req in existingReqs:
		# Check if the request is from the same month
		date = req['date']
		if date.month == now.month and date.year == now.year:
			month = date.strftime('%B')
			await ctx.respond(f'You already have a request for {month}:\n\t*{req["title"]} ({req["year"]})*\nPlease wait until the next month to request again.')
			return
	
	# Add the request to the database
	ref.add({
		'user_id': user,
		'user_name': ctx.author.name,
		'title': title,
		'year': year,
		'date': now,
		'picked': False,
	})

	await ctx.respond(f':up_arrow: Requested **{title} ({year})**!')

@bot.slash_command(description='View the current movie request list.')
async def requests(ctx):
	ref = getRef()

	# Get all requests that are not picked
	reqs = ref.where(filter=FieldFilter('picked', '==', False)).stream()
	rawRequests = [doc for doc in reqs]
	if rawRequests == []:
		await ctx.respond('There are no requests at the moment.')
		return

	# Turn them into a list of dictionaries
	reqs = []
	for req in rawRequests:
		reqs.append(req.to_dict())
	
	# Build the response
	res = 'Current Requests:\n'
	for req in reqs:
		res += f'\t• {req["title"]} (*{req["year"]}*) by **{req["user_name"]}**\n'
	
	await ctx.respond(res)

@bot.slash_command(description='Pick a given movie from the request list.')
async def roll(ctx):
	user = str(ctx.author.id)
	ref  = getRef()
	# Get metadata
	metaRef = getMeta()
	metadata = metaRef.get().to_dict()

	# Check if the user is in the allowed rollers list
	if user not in ALLOWED_ROLLERS:
		await ctx.respond('You are not allowed to use this command!')
		return
	
	# Get all requests that are not picked and not from the last picker (if it crashes the bot will pick skip)
	try:
		reqs = ref.where(filter=FieldFilter('picked', '==', False)).where(filter=FieldFilter('user_id', '!=', metadata['last_id'])).stream()
		requests = [doc for doc in reqs]
	except:
		await ctx.respond('There are no valid requests at the moment.')
		return
	
	# Multiply each request by the number of months since it was requested
	# So if it's been up for 3 months, add 3 copies of it to the list
	newRequests = []
	for req in requests:
		reqDict = req.to_dict()
		months = getMonthsSince(reqDict['date'])
		# Append once and then x more times depending on time in the q
		newRequests.append(req)
		if months > 0: newRequests.extend([req] * months)
	
	# Pick a random request
	pickedReq = random.choice(newRequests)
	reqDict = pickedReq.to_dict()

	# Mark the request as picked
	ref.document(pickedReq.id).update({
		'picked': True
	})
	# Update the metadata of the last picker
	metaRef.update({
		'last_id': reqDict['user_id']
	})

	await ctx.respond(f':down_arrow: Picked {reqDict["title"]} (*{reqDict["year"]}*) by **{reqDict["user_name"]}**')


####################
# Initalize the bot
if __name__ == '__main__':
	# Check if token is valid
	token = os.getenv('BOT_TOKEN_DEV') if _dev else os.getenv('BOT_TOKEN_PROD')
	if token is None:
		print(' ERR > Token is invalid!')
		exit()

	# Start the bot, catch CTRL+C
	try:
		bot.run(token)
	except KeyboardInterrupt:
		print('LOG > CTRL+C detected, exiting...')