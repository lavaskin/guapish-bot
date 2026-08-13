# GUAPISH Discord Bot

A bot for the GUAPISH podcast Discord server. Used for general informaation as well as the movie requesting system.

To join the Discord server, subscribe to the [GUAPISH Patreon](https://www.patreon.com/GUAPISH).

## Commands

- `/roll`: Rolls the current Patreon requests and picks the next movie to be watched. Only usable if the roller's discord uid is in `ALLOWED_ROLLERS`.
- `/request <title> <year>`: Requests a given movie for the current month. Only allowed 1 request per month. Only usable by users with the Patreon role.
- `/requests`: Prints all the current movie requests.
- `/myrequests`: Prints all of the users requested movies and their % chance of being picked with the roll command.

## Setup

- Install the dependencies with poetry using ```poetry install``` (creates a project-local `.venv`).
- Fill out the needed fields referenced in ```.env-example.txt``` in a new ```.env``` file.
- Get a ```firebase.json``` file from your Firebase app to hookup to Firestore with.
- Run with ```./run``` (uses `.venv/bin/python` directly).

## Project structure

- `bot.py` — process entry; loads shared events and feature cogs
- `src/core/` — shared bot, config, firebase, pagination
- `src/features/` — one package per feature; register new cogs in `FEATURES`
