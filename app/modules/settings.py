import os
from .logging import logging

telegram_api_token = os.getenv("TELEGRAM_BOT_TOKEN")
log_level = os.getenv("LOG_LEVEL", "INFO")
allowed_chat_ids = os.getenv("ALLOWED_CHAT_IDS", "any").split(",")
# Question and answers settings
question = os.getenv("QUESTION","Чий Крим?")
good_answer = os.getenv("GOOD_ANSWER","🇺🇦")
bad_answer = os.getenv("BAD_ANSWER","я нэ знаю")
timeout = 180  # Timeout in seconds


try:
    # Open the help text file and read its contents
    with open('data/help.txt', 'r') as f:
        help_text = f.read()
except FileNotFoundError:
    # If the file does not exist, set a default help text
    help_text = "Sorry, the help file is not available at the moment."

logging.info("Settings loaded")
