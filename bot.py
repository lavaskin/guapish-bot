from src.core.bot import create_bot
from src.core.events import CoreCog
from src.features import FEATURES


bot = create_bot()
bot.add_cog(CoreCog(bot))
for feature in FEATURES:
	bot.add_cog(feature(bot))


if __name__ == '__main__':
	token = bot.app_config.bot_token
	if not token:
		print(' ERR > Token is invalid!')
		exit(1)

	try:
		bot.run(token)
	except KeyboardInterrupt:
		print('LOG > CTRL+C detected, exiting...')
