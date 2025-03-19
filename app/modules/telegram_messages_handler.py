from .logging import logging
from .settings import telegram_api_token, question, good_answer, bad_answer, timeout
import asyncio
import random
from faker import Faker

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, Application, CommandHandler, MessageHandler, filters, CallbackContext
from telegram.error import BadRequest

fake = Faker()
jobs_dict = {} # When a user is invited by another user, the bot clears context.user_data due to how the Telegram library handles new chat members.
# This happens because context.user_data is tied to individual updates, and new member events reset the context.
# To persist tasks like timeout jobs across updates, we use a global dictionary (jobs_dict) as a workaround.


async def new_chat_members(update: Update, context: CallbackContext) -> None:
    """Handle new chat members by sending them a verification message after a delay."""
    logging.debug (f"context.user_data at function start: {context.user_data}")
    if update.message.text and update.message.text.startswith("/new"):
        # This is a command, not a new chat member
        logging.info("This is a command, not a new chat member.")
        member =  update.message.from_user
        await send_verification_message(update, context, member)
    else:
        new_members = update.message.new_chat_members
        logging.info(f"New chat members: {new_members}")
        await asyncio.sleep(3)  # Wait for 3 seconds before sending the verification message
        for member in new_members:
            await send_verification_message(update, context, member)
    logging.debug (f"context.user_data at function end: {context.user_data}")


async def send_verification_message(update: Update, context: CallbackContext, user) -> None:
    """Send a verification message to the user with a set of answers to choose from."""
    logging.debug (f"context.user_data at function start: {context.user_data}")

    # Generate additional random emojis
    additional_answers = [fake.emoji() for _ in range(2)]

    # Ensure the additional answers are unique and not equal to good or bad answer
    for i in range(len(additional_answers)):
        while additional_answers[i] == good_answer or additional_answers[i] == bad_answer or additional_answers[i] in additional_answers[:i]:
            additional_answers[i] = fake.word()

    # Combine all answers and shuffle them
    answers = [good_answer, bad_answer] + additional_answers
    random.shuffle(answers)

    # Compose the verification message
    text = f"Hello, {user.mention_html()}! To continue the conversation, please select the correct answer."
    text += f"\n\nYou have {timeout} seconds."
    text += f"\n\n{question}"

    # Create the inline keyboard buttons
    keyboard = [[InlineKeyboardButton(text=answer, callback_data=f"verify_{user.id}_{answer}") for answer in answers]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    logging.info(f"Sending verification message to user {user}.")

    sent_message = await context.bot.send_message(chat_id=update.effective_chat.id, text=text, reply_markup=reply_markup, parse_mode='HTML')

    # Set a timeout for the user to answer
    logging.info(f"Setting timeout for user {user}.")
    context.user_data[f"message_{user.id}"] = sent_message
    # Set a task to kick the user if they don't answer in time
    jobs_dict[f'timeout_task_{user.id}'] = asyncio.create_task(timeout_kick(update, context, user, timeout))
    logging.debug (f"context.user_data at function end: {context.user_data}")

async def timeout_kick(update: Update, context: CallbackContext, user, timeout: int):
    """Kick the user if they don't respond in time."""
    logging.debug (f"context.user_data at function start: {context.user_data}")
    remaining_time = timeout
    half_time_sent = False
    step = 1
    logging.info(f"Timeout for user {user} is {timeout} seconds.")
    while remaining_time > 0:
        if remaining_time < timeout // 2 and not half_time_sent:
            # Send a reminder to the user
            reminder_text = f"{remaining_time} seconds left for user {user.mention_html()} to answer"
            reminder_text = f"{remaining_time} seconds left for user {user.mention_html()} to answer.\n\n{question}\n\nPlease select the correct answer from the options provided."
            await context.bot.send_message(chat_id=update.effective_chat.id, text=reminder_text, parse_mode='HTML')
            logging.info(f"User {user.id} has {remaining_time} seconds left to respond.")
            half_time_sent = True
        await asyncio.sleep(step)
        remaining_time -= step

    if not context.user_data.get(f'verified_{user.id}', False):
        # The user did not respond in time
        logging.info(f"User {user.id} did not respond in time. Kicking.")
        await context.bot.send_message(chat_id=update.effective_chat.id, text=f"User {user.id} did not respond in time. Kicking.")
        await kick_user(update, context, user.id)
        # Delete the message if it still exists
        if f"message_{user.id}" in context.user_data:
            message_to_delete = context.user_data[f"message_{user.id}"]
            await message_delete(message_to_delete)
    logging.debug (f"context.user_data at function end: {context.user_data}")

async def kick_user(update: Update, context: CallbackContext, user_id: int) -> None:
    """Kick the user out of the chat and unban them after a delay."""
    await context.bot.ban_chat_member(chat_id=update.effective_chat.id, user_id=user_id)
    logging.info(f"User {user_id} has been kicked.")
    await asyncio.sleep(5)  # Wait for 5 seconds before unbanning the user
    await context.bot.unban_chat_member(chat_id=update.effective_chat.id, user_id=user_id)
    logging.info(f"User {user_id} has been unbanned.")

async def message_delete(message) -> None:
    """Delete the message if it exists."""
    try:
        await message.delete()
    except BadRequest:
        logging.info("Message already deleted or not found.")

async def handle_answer(update: Update, context: CallbackContext) -> None:
    """Handle the user's answer to the verification question."""
    logging.debug (f"context.user_data at function start: {context.user_data}")
    logging.info(f"Handling answer: {update.callback_query.data}")
    query = update.callback_query
    user_id, answer = query.data.split('_')[1:]
    user_id = int(user_id)

    # Check if the response is from the user who was asked the question
    if query.from_user.id != user_id:
        logging.info(f"User {query.from_user.id} is not the user who was asked the question ({user_id}).")
        text = f"Hey! {query.from_user.mention_html()}! You are not the user who was asked the question."
        await context.bot.send_message(chat_id=update.effective_chat.id, text=text, parse_mode='HTML')
        return

    # Now process the answer
    if answer == good_answer:
        await context.bot.send_message(chat_id=update.effective_chat.id, text=f"User {user_id} provided the correct answer.")
        logging.info(f"User {user_id} provided the correct answer.")
        context.user_data[f'verified_{user_id}'] = True
        # Additional correct answer handling here
    else:
        logging.info(f"User {user_id} provided an incorrect answer.")
        await context.bot.send_message(chat_id=update.effective_chat.id, text=f"User {user_id} provided an incorrect answer. Kicking.")
        await kick_user(update, context, user_id)
    logging.info(f"Deleting message for user {user_id}.")
    await message_delete(query.message)
    logging.info(f"Deleting timeout task for user {user_id}.")

    task = jobs_dict.pop(f'timeout_task_{user_id}', None)
    if task is None:
        logging.warning(f"No timeout task found for user {user_id} in jobs_dict.")
    logging.info(f"task: {task}")
    if task:
        task.cancel()
    logging.debug (f"context.user_data at function end: {context.user_data}")

async def ping_command(update: Update, context: CallbackContext) -> None:
    """Respond to the /ping command with 'pong'."""
    await update.message.reply_html("pong", disable_web_page_preview=True)

def main() -> None:
    """
    Start the bot and add command handlers.

    This bot is designed to handle new chat members by verifying them with a question.
    It includes the following functionalities:
    - Responds to the /ping command with "pong".
    - Handles the /new command to manually trigger the verification process for a user.
    - Automatically verifies new chat members when they join the chat.
    - Processes user answers to the verification question and takes appropriate actions (e.g., kicking users for incorrect answers or timeouts).

    Handlers added:
    - CommandHandler("ping", ping_command): Responds to the /ping command.
    - CommandHandler("new", new_chat_members): Manually triggers the verification process.
    - MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, new_chat_members): Automatically handles new chat members.
    - CallbackQueryHandler(handle_answer, pattern=r'^verify_\\d+_.+$'): Processes answers to the verification question.
    """
    """Start the bot and add command handlers."""
    application = Application.builder().token(telegram_api_token).build()

    # Add handlers to the application
    application.add_handler(CommandHandler("ping", ping_command))

    application.add_handler(CommandHandler("new", new_chat_members))

    application.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, new_chat_members))

    application.add_handler(CallbackQueryHandler(handle_answer, pattern=r'^verify_\d+_.+$'))

    # Run the bot
    application.run_polling()

if __name__ == "__main__":
    main()
