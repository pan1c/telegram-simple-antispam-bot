import asyncio
import random

from faker import Faker
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    CallbackContext,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

from .logging import logging
from .settings import (
    bad_answer,
    good_answer,
    question,
    telegram_api_token,
    timeout,
    unban_delay_seconds,
)

fake = Faker()
jobs_dict = {}
verification_sessions = {}

VERIFICATION_DELAY_SECONDS = 3
GOOD_ANSWER_CALLBACK = "good"
BAD_ANSWER_CALLBACK = "bad"
DECOY_ANSWER_CALLBACKS = ("decoy_1", "decoy_2")


def get_session_key(chat_id: int, user_id: int) -> tuple[int, int]:
    return chat_id, user_id


def get_timeout_task_key(chat_id: int, user_id: int) -> tuple[int, int]:
    return chat_id, user_id


def clear_verification_session(chat_id: int, user_id: int) -> None:
    verification_sessions.pop(get_session_key(chat_id, user_id), None)


async def new_chat_members(update: Update, context: CallbackContext) -> None:
    """Handle new chat members by sending them a verification message after a delay."""
    logging.debug(f"context.user_data at function start: {context.user_data}")
    if update.message.text and update.message.text.startswith("/new"):
        logging.info("This is a command, not a new chat member.")
        member = update.message.from_user
        await send_verification_message(update, context, member)
    else:
        new_members = update.message.new_chat_members
        logging.info(f"New chat members: {new_members}")
        await asyncio.sleep(VERIFICATION_DELAY_SECONDS)
        for member in new_members:
            await send_verification_message(update, context, member)
    logging.debug(f"context.user_data at function end: {context.user_data}")


async def send_verification_message(update: Update, context: CallbackContext, user) -> None:
    """Send a verification message to the user with a set of answers to choose from."""
    logging.debug(f"context.user_data at function start: {context.user_data}")
    chat_id = update.effective_chat.id
    session_key = get_session_key(chat_id, user.id)
    task_key = get_timeout_task_key(chat_id, user.id)

    existing_task = jobs_dict.pop(task_key, None)
    if existing_task:
        existing_task.cancel()
    clear_verification_session(chat_id, user.id)

    additional_answers = [fake.emoji() for _ in range(2)]

    for i in range(len(additional_answers)):
        while (
            additional_answers[i] == good_answer
            or additional_answers[i] == bad_answer
            or additional_answers[i] in additional_answers[:i]
        ):
            additional_answers[i] = fake.word()

    answers = [
        (GOOD_ANSWER_CALLBACK, good_answer),
        (BAD_ANSWER_CALLBACK, bad_answer),
        (DECOY_ANSWER_CALLBACKS[0], additional_answers[0]),
        (DECOY_ANSWER_CALLBACKS[1], additional_answers[1]),
    ]
    random.shuffle(answers)

    text = f"Hello, {user.mention_html()}! To continue the conversation, please select the correct answer."
    text += f"\n\nYou have {timeout} seconds."
    text += f"\n\n{question}"

    keyboard = [[
        InlineKeyboardButton(
            text=answer_text,
            callback_data=f"verify:{user.id}:{answer_id}",
        )
        for answer_id, answer_text in answers
    ]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    logging.info(f"Sending verification message to user {user}.")
    sent_message = await context.bot.send_message(
        chat_id=chat_id,
        text=text,
        reply_markup=reply_markup,
        parse_mode="HTML",
    )

    logging.info(f"Setting timeout for user {user}.")
    verification_sessions[session_key] = {
        "message": sent_message,
        "verified": False,
    }
    jobs_dict[task_key] = asyncio.create_task(timeout_kick(update, context, user, timeout))
    logging.debug(f"context.user_data at function end: {context.user_data}")


async def timeout_kick(update: Update, context: CallbackContext, user, timeout: int) -> None:
    """Kick the user if they don't respond in time."""
    logging.debug(f"context.user_data at function start: {context.user_data}")
    chat_id = update.effective_chat.id
    session_key = get_session_key(chat_id, user.id)
    task_key = get_timeout_task_key(chat_id, user.id)
    remaining_time = timeout
    half_time_sent = False
    step = 1
    logging.info(f"Timeout for user {user} is {timeout} seconds.")

    while remaining_time > 0:
        if remaining_time < timeout // 2 and not half_time_sent:
            reminder_text = (
                f"{remaining_time} seconds left for user {user.mention_html()} to answer.\n\n"
                f"{question}\n\nPlease select the correct answer from the options provided."
            )
            await context.bot.send_message(chat_id=chat_id, text=reminder_text, parse_mode="HTML")
            logging.info(f"User {user.id} has {remaining_time} seconds left to respond.")
            half_time_sent = True
        await asyncio.sleep(step)
        remaining_time -= step

    session = verification_sessions.get(session_key)
    if session and not session.get("verified", False):
        logging.info(f"User {user.id} did not respond in time. Kicking.")
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"{user.mention_html()} did not respond in time. Kicking.",
            parse_mode="HTML",
        )
        await kick_user(update, context, user.id)
        message_to_delete = session.get("message")
        if message_to_delete:
            await message_delete(message_to_delete)
        clear_verification_session(chat_id, user.id)

    jobs_dict.pop(task_key, None)
    logging.debug(f"context.user_data at function end: {context.user_data}")


async def kick_user(update: Update, context: CallbackContext, user_id: int) -> None:
    """Kick the user out of the chat and unban them after a delay."""
    await context.bot.ban_chat_member(chat_id=update.effective_chat.id, user_id=user_id)
    logging.info(f"User {user_id} has been kicked.")
    await asyncio.sleep(unban_delay_seconds)
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
    logging.debug(f"context.user_data at function start: {context.user_data}")
    logging.info(f"Handling answer: {update.callback_query.data}")
    query = update.callback_query
    _, user_id, answer_id = query.data.split(":", 2)
    user_id = int(user_id)
    chat_id = update.effective_chat.id
    session_key = get_session_key(chat_id, user_id)
    task_key = get_timeout_task_key(chat_id, user_id)
    session = verification_sessions.get(session_key)

    if session is None:
        await query.answer("Verification request is no longer active.", show_alert=True)
        return

    if query.from_user.id != user_id:
        logging.info(f"User {query.from_user.id} is not the user who was asked the question ({user_id}).")
        text = f"Hey! {query.from_user.mention_html()}! You are not the user who was asked the question."
        await context.bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML")
        await query.answer()
        return

    if answer_id == GOOD_ANSWER_CALLBACK:
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"{query.from_user.mention_html()} provided the correct answer.",
            parse_mode="HTML",
        )
        logging.info(f"User {user_id} provided the correct answer.")
        session["verified"] = True
    else:
        logging.info(f"User {user_id} provided an incorrect answer.")
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"{query.from_user.mention_html()} provided an incorrect answer. Kicking.",
            parse_mode="HTML",
        )
        await kick_user(update, context, user_id)

    await query.answer()
    logging.info(f"Deleting message for user {user_id}.")
    await message_delete(query.message)
    logging.info(f"Deleting timeout task for user {user_id}.")

    task = jobs_dict.pop(task_key, None)
    if task is None:
        logging.warning(f"No timeout task found for user {user_id} in jobs_dict.")
    logging.info(f"task: {task}")
    if task:
        task.cancel()
    clear_verification_session(chat_id, user_id)
    logging.debug(f"context.user_data at function end: {context.user_data}")


async def ping_command(update: Update, context: CallbackContext) -> None:
    """Respond to the /ping command with 'pong'."""
    await update.message.reply_html("pong", disable_web_page_preview=True)


def main() -> None:
    """Start the bot and add command handlers."""
    application = Application.builder().token(telegram_api_token).build()

    application.add_handler(CommandHandler("ping", ping_command))
    application.add_handler(CommandHandler("new", new_chat_members))
    application.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, new_chat_members))
    application.add_handler(CallbackQueryHandler(handle_answer, pattern=r"^verify:\d+:[^:]+$"))

    application.run_polling()


if __name__ == "__main__":
    main()
