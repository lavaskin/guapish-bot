from datetime import datetime
import random
import discord
import os
from dotenv import load_dotenv
from firebase_admin import credentials, firestore, initialize_app


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
initialize_app(_dbCred)
DB = firestore.client()


# Helper Functions
def getRef() -> firestore.CollectionReference:
	return DB.collection(COLLECTION)
	

# Bot Setup
bot = discord.Bot()

@bot.event
async def on_application_command_error(ctx):
	print(f' ERR > \n{ctx}')

@bot.event
async def on_ready():
    print('LOG > Bot is ready!')
    

# Commands
@bot.slash_command(description='Request a given movie each month. Requests reset at the start of each month (PST).')
async def request(ctx, title: str, year: int):
	user = str(ctx.author.id)
	ref  = getRef()
	now = datetime.now()

	# Check if the user has the Patreon role
	foundRole = False
	for role in ctx.author.roles:
		if str(role.id) == PATREON_ROLE:
			foundRole = True
			break
	if not foundRole:
		await ctx.respond('You must be a Patreon sub to use this command! Subscribe here:\n\t*https://www.patreon.com/GUAPISH*')
		return

	# Check if user already has a request by checking if any of the documents have the same user id in the 'user' field
	# as well as not being picked yet
	existingReqs = ref.where('user', '==', user).where('picked', '==', False).stream()
	for req in existingReqs:
		reqObj = req.to_dict()
		# Check if the request is from the same month
		date = reqObj['date']
		if date.month == now.month and date.year == now.year:
			month = date.strftime('%B')
			title = reqObj['title']
			year  = reqObj['year']
			await ctx.respond(f'You already have a request for {month}:\n\t*{title} ({year})*\nPlease wait until the next month to request again.')
			return
	
	# Add the request to the database
	ref.add({
		'user': user,
		'title': title,
		'year': year,
		'date': now,
		'picked': False,
	})

	await ctx.respond(f'Requested **{title} ({year})**!')

@bot.slash_command(description='View the current movie request list.')
async def requests(ctx):
	ref = getRef()

	# Get all requests
	reqs = ref.stream()
	if reqs is None:
		await ctx.respond('There are no requests at the moment.')
		return
	
	# Build the response
	res = 'Current Requests:\n'
	for req in reqs:
		reqObj = req.to_dict()
		res += f'\t - {reqObj["title"]} ({reqObj["year"]}) by *<@{reqObj["user"]}>*\n'
	
	await ctx.respond(res)

@bot.slash_command(description='Pick a given movie from the request list.')
async def roll(ctx):
	user = str(ctx.author.id)
	ref  = getRef()

	# Check if the user is in the allowed rollers list
	if user not in ALLOWED_ROLLERS:
		await ctx.respond('You are not allowed to use this command!')
		return
	
	# Get all requests that are not picked
	reqs = ref.where('picked', '==', False)
	requests = [doc for doc in reqs.stream()]

	if requests == []:
		await ctx.respond('There are no requests at the moment.')
		return
	
	# Pick a random request
	pickedReq = random.choice(requests)
	reqDict = pickedReq.to_dict()

	# Mark the request as picked
	ref.document(pickedReq.id).update({
		'picked': True
	})

	await ctx.respond(f'Picked {reqDict["title"]} ({reqDict["year"]}) by *<@{reqDict["user"]}>*')


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