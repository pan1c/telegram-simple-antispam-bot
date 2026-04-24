import os
from .logging import logging

telegram_api_token = os.getenv("TELEGRAM_BOT_TOKEN")
log_level = os.getenv("LOG_LEVEL", "INFO")
# Question and answers settings
question = os.getenv("QUESTION", "Чий Крим?")
good_answer = os.getenv("GOOD_ANSWER", "🇺🇦")
bad_answer = os.getenv("BAD_ANSWER", "я нэ знаю")

try:
    timeout = int(os.getenv("TIMEOUT", "180"))
except ValueError:
    logging.warning("Invalid TIMEOUT value provided, using default 180 seconds.")
    timeout = 180

try:
    unban_delay_seconds = int(os.getenv("UNBAN_DELAY_SECONDS", "5"))
except ValueError:
    logging.warning("Invalid UNBAN_DELAY_SECONDS value provided, using default 5 seconds.")
    unban_delay_seconds = 5


try:
    # Open the help text file and read its contents
    with open("data/help.txt", "r") as f:
        help_text = f.read()
except FileNotFoundError:
    # If the file does not exist, set a default help text
    help_text = "Sorry, the help file is not available at the moment."

logging.info("Settings loaded")
