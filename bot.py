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

def getRequestEntries(request) -> int:
	return getMonthsSince(request['date']) + 1

def getRequests(ref):
	# Get all requests that are not picked
	rawRequests = ref.where(filter=FieldFilter('picked', '==', False)).stream()
	reqs = [doc.to_dict() for doc in rawRequests]

	# Sort the movies by date requested and return them
	sortedReqs = sorted(reqs, key=lambda x: x['date'], reverse=True)
	return sortedReqs
	

############
# Bot Setup
bot = discord.Bot()

@bot.event
async def on_application_command_error(ctx, error):
	print(f' ERR ({ctx.command}) > {error}')

	await ctx.respond('An error occured while processing your command...', ephemeral=True)

@bot.event
async def on_ready():
    print('LOG > Bot is ready!\n')
    

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
		await ctx.respond('You must be a Patreon sub to use this command! Subscribe here:\n\t*https://www.patreon.com/GUAPISH*', ephemeral=True)
		return
	
	# Check that the year is valid
	if year < 1890 or year > now.year + 1:
		await ctx.respond(f'Invalid year: **{year}**. Please enter one between 1890 and now.', ephemeral=True)
		return
	
	# Log request Info
	print(f'LOG > Requested by {ctx.author.name} ({user}): {title} ({year})')

	# Check if user already has a request by checking if any of the documents have the same user id in the 'user' field
	existingReqs = [doc.to_dict() for doc in ref.where(filter=FieldFilter('user_id', '==', user)).stream()]
	for req in existingReqs:
		# Check if the request is from the same month
		date = req['date']
		if date.month == now.month and date.year == now.year:
			month = date.strftime('%B')
			await ctx.respond(f'You already have a request for {month}:\n\t*{req["title"]} ({req["year"]})*\nPlease wait until the next month to request again.', ephemeral=True)
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

	# Get requests
	reqs = getRequests(ref)
	
	# Build the response
	res = ''
	for req in reqs:
		res += f'- {req["title"]} ({req["year"]})\n'

	await ctx.respond(res, ephemeral=True)

@bot.slash_command(description='View all your current requests, as well as their percent chance of being picked.')
async def myrequests(ctx):
	ref = getRef()
	uid = str(ctx.author.id)

	# Get requests
	reqs = getRequests(ref)
	res = ''

	# Calculate the pick percentage chance for each movie
	# This is done by first finding out how many total movies there are by getting their additive value from being in queue a long time
	# Then taking that to do a generic % calc
	totalEntries = 0
	for req in reqs:
		# Get how long it's been in q to add more entries for it (the +1 is movies that got requested this month are 0)
		totalEntries += getRequestEntries(req)
	# Loop again to calc % chance
	for req in reqs:
		# Skip non user requests
		if req['user_id'] == uid:
			entries = getRequestEntries(req)
			percent = round((entries / totalEntries) * 100, 1)
			res += f'- {req["title"]} ({req["year"]}) [{percent}%]\n'
	
	# Check if there were any requests
	if res == '':
		await ctx.respond('You have no current requests!', ephemeral=True)
		return
	
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
		await ctx.respond('You are not allowed to use this command!', ephemeral=True)
		return
	
	# Get all requests that are not picked and not from the last picker (if it crashes the bot will pick skip)
	try:
		rawRequests = ref.where(filter=FieldFilter('picked', '==', False)).where(filter=FieldFilter('user_id', '!=', metadata['last_id'])).stream()
		reqs = [doc for doc in rawRequests]
	except:
		await ctx.respond('There are no valid requests at the moment.')
		return
	
	# Multiply each request by the number of months since it was requested
	# So if it's been up for 3 months, add 3 copies of it to the list
	newRequests = []
	for req in reqs:
		reqDict = req.to_dict()
		months = getMonthsSince(reqDict['date'])
		# Append once and then x more times depending on time in the q
		newRequests.append(req)
		if months > 0: newRequests.extend([req] * months)
	
	# Pick a random request
	pickedReq = random.choice(newRequests)
	reqDict = pickedReq.to_dict()

	print(f'LOG > "{user}" picked {reqDict["year"]} ({reqDict["year"]})')

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