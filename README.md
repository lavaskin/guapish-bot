# GUAPISH Discord Bot

A bot for the GUAPISH podcast Discord server. Used for general informaation as well as the movie requesting system.

To join the Discord server, subscribe to the [GUAPISH Patreon](https://www.patreon.com/GUAPISH).

## Commands

- `/roll`: Rolls the current Patreon requests and picks the next movie to be watched. Only usable if the roller's discord uid is in the ALLOWED_ROLLERS array.
- `/request <title> <year>`: Requests a given movie for the current month. Only allowed 1 request per month. Only usable by users with the Patreon role.
- `/requests`: Prints all the current movie requests.
- `/myrequests`: Prints all of the users requested movies and their % chance of being picked with the roll command.
