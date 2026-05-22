import random
from datetime import datetime

from src.helpers import create_bot, get_all_requests, get_months_since, get_request_entries

from google.cloud.firestore_v1.base_query import FieldFilter


ALLOWED_ROLLERS = ['148907812670406656', '373724550350897154', '289947773183197185']


#############
# Bot Setup #
bot = create_bot()

@bot.event
async def on_application_command_error(ctx, error):
	print(f' ERR ({ctx.command}) > {error}')

	await ctx.respond('An error occurred while processing your command...', ephemeral=True)

@bot.event
async def on_ready():
    print('LOG > Bot Running\n')
    

############
# Commands #
@bot.slash_command(description='Request a given movie each month. Requests reset at the start of each month (PST).')
async def request(ctx, title: str, year: int):
	user = str(ctx.author.id)
	ref = bot.firebase_config.get_requests_ref()
	now = datetime.now()

	# Check if the user has the Patreon role
	roles = [str(role.id) for role in ctx.author.roles]
	if bot.app_config.patreon_role not in roles:
		await ctx.respond('You must be a Patreon sub to use this command! Subscribe here:\n\t*https://www.patreon.com/GUAPISH*', ephemeral=True)
		return
	
	# Check that the year is valid
	if year < 1890 or year > now.year + 1:
		await ctx.respond(f'Invalid year: **{year}**. Please enter one between 1890 and now.', ephemeral=True)
		return

	# Check if user already has a request by checking if any of the documents have the same user id in the 'user' field
	existing_requests = [doc.to_dict() for doc in ref.where(filter=FieldFilter('user_id', '==', user)).stream()]
	for req in existing_requests:
		# Check if the request is from the same month
		date = req['date']
		if date.month == now.month and date.year == now.year:
			print(f'LOG > Double Request: {ctx.author.name} already requested \"{req["title"]} ({req["year"]})\"')
			month = date.strftime('%B')
			await ctx.respond(f'You already have a request for {month}:\n\t*{req["title"]} ({req["year"]})*\nPlease wait until the next month to request again.', ephemeral=True)
			return
	
	# Log request Info
	print(f'LOG > Requested by {ctx.author.name} ({user}): {title} ({year})')

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
	ref = bot.firebase_config.get_requests_ref()

	# Get requests
	requests = get_all_requests(ref)
	
	# Build the response
	res = ''
	for req in requests:
		res += f'- {req["title"]} ({req["year"]})\n'

	await ctx.respond(res, ephemeral=True)

@bot.slash_command(name='myrequests', description='View all your current requests, as well as their percent chance of being picked.')
async def my_requests(ctx):
	ref = bot.firebase_config.get_requests_ref()
	uid = str(ctx.author.id)

	# Get requests
	requests = get_all_requests(ref)
	res = ''

	# Calculate the pick percentage chance for each movie
	# This is done by first finding out how many total movies there are by getting their additive value from being in queue a long time
	# Then taking that to do a generic % calc
	totalEntries = 0
	for req in requests:
		# Get how long it's been in q to add more entries for it (the +1 is movies that got requested this month are 0)
		totalEntries += get_request_entries(req)
	# Loop again to calc % chance
	total_chance = 0
	for req in requests:
		# Skip non user requests
		if req['user_id'] == uid:
			months = get_months_since(req['date'])
			entries = get_request_entries(req)
			percent = round((entries / totalEntries) * 100, 1)
			total_chance += percent
			res = f'1. {req["title"]} ({req["year"]}) [{percent}%, {months} months]\n' + res
	
	# Check if there were any requests
	if res == '':
		await ctx.respond('You have no current requests!', ephemeral=True)
		return
	
	# Add the combined total and send it
	res += f'**Combined Chance**: {round(total_chance, 1)}%'
	await ctx.respond(res)


@bot.slash_command(name='roll', description='Pick a given movie from the request list.')
async def roll(ctx):
	user = str(ctx.author.id)

	ref  = bot.firebase_config.get_requests_ref()
	metaRef = bot.firebase_config.get_metadata_doc()
	metadata = bot.firebase_config.get_metadata()

	# Check if the user is in the allowed rollers list
	if user not in ALLOWED_ROLLERS:
		await ctx.respond('You are not allowed to use this command!', ephemeral=True)
		return
	
	# Get all requests that are not picked and not from the last picker (if it crashes the bot will pick skip)
	try:
		rawRequests = ref.where(filter=FieldFilter('picked', '==', False)).where(filter=FieldFilter('user_id', '!=', metadata['last_id'])).stream()
		requests = [doc for doc in rawRequests]
	except:
		await ctx.respond('There are no valid requests at the moment.')
		return
	
	# Add extra entires for movies that have been in the queue longer
	newRequests = []
	for req in requests:
		reqDict = req.to_dict()
		entries = get_request_entries(reqDict)
		newRequests.extend([req] * entries)
	
	# Pick a random request
	pickedReq = random.choice(newRequests)
	reqDict = pickedReq.to_dict()

	print(f'LOG > Rolled {reqDict["title"]} ({reqDict["year"]})')

	# Mark the request as picked
	ref.document(pickedReq.id).update({
		'picked': True
	})
	# Update the metadata of the last picker
	metaRef.update({
		'last_id': reqDict['user_id']
	})

	await ctx.respond(f':down_arrow: Picked {reqDict["title"]} (*{reqDict["year"]}*) by **{reqDict["user_name"]}**')


######################
# App Initialization #
if __name__ == '__main__':
	# Check if token is valid
	token = bot.app_config.bot_token
	if token is None:
		print(' ERR > Token is invalid!')
		exit(1)

	# Start the bot & catch CTRL+C
	try:
		bot.run(token)
	except KeyboardInterrupt:
		print('LOG > CTRL+C detected, exiting...')
