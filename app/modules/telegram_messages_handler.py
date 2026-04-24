import asyncio
import random

from faker import Faker
from telegram import ChatPermissions, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    CallbackContext,
    CallbackQueryHandler,
    ChatMemberHandler,
    CommandHandler,
    MessageHandler,
    TypeHandler,
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
seen_chat_ids = set()

VERIFICATION_DELAY_SECONDS = 1
GOOD_ANSWER_CALLBACK = "good"
BAD_ANSWER_CALLBACK = "bad"
DECOY_ANSWER_CALLBACKS = ("decoy_1", "decoy_2")
JOINED_CHAT_STATUSES = {"member", "administrator", "restricted"}
LEFT_CHAT_STATUSES = {"left", "kicked"}
MUTED_PERMISSIONS = ChatPermissions(
    can_send_messages=False,
    can_send_media_messages=False,
    can_send_polls=False,
    can_send_other_messages=False,
    can_add_web_page_previews=False,
    can_send_audios=False,
    can_send_documents=False,
    can_send_photos=False,
    can_send_videos=False,
    can_send_video_notes=False,
    can_send_voice_notes=False,
)
UNMUTED_PERMISSIONS = ChatPermissions(
    can_send_messages=True,
    can_send_media_messages=True,
    can_send_polls=True,
    can_send_other_messages=True,
    can_add_web_page_previews=True,
    can_send_audios=True,
    can_send_documents=True,
    can_send_photos=True,
    can_send_videos=True,
    can_send_video_notes=True,
    can_send_voice_notes=True,
)


def get_session_key(chat_id: int, user_id: int) -> tuple[int, int]:
    return chat_id, user_id


def get_timeout_task_key(chat_id: int, user_id: int) -> tuple[int, int]:
    return chat_id, user_id


def clear_verification_session(chat_id: int, user_id: int) -> None:
    verification_sessions.pop(get_session_key(chat_id, user_id), None)


def add_session_message(chat_id: int, user_id: int, message) -> None:
    session = verification_sessions.get(get_session_key(chat_id, user_id))
    if session is not None and message is not None:
        session.setdefault("messages", []).append(message)


def format_chat(chat) -> str:
    if chat is None:
        return "chat=None"

    title = getattr(chat, "title", None) or getattr(chat, "username", None) or getattr(chat, "first_name", None)
    return f"chat_id={chat.id}, type={chat.type}, title={title!r}"


def format_user(user) -> str:
    if user is None:
        return "user=None"

    username = f"@{user.username}" if getattr(user, "username", None) else None
    name = username or getattr(user, "full_name", None) or getattr(user, "first_name", None)
    return f"user_id={user.id}, name={name!r}, is_bot={user.is_bot}"


def format_chat_member(chat_member) -> str:
    if chat_member is None:
        return "member=None"

    attrs = [
        "status",
        "can_restrict_members",
        "can_delete_messages",
        "can_invite_users",
        "can_manage_chat",
        "can_promote_members",
    ]
    details = [format_user(getattr(chat_member, "user", None))]
    details.extend(
        f"{attr}={getattr(chat_member, attr)}"
        for attr in attrs
        if hasattr(chat_member, attr)
    )
    return ", ".join(details)


def remember_chat(chat, source: str) -> None:
    if chat is None:
        return

    if chat.id not in seen_chat_ids:
        seen_chat_ids.add(chat.id)
        logging.info(f"Seen chat via {source}: {format_chat(chat)}")


async def get_current_bot_id(context: CallbackContext) -> int:
    try:
        bot_id = getattr(context.bot, "id", None)
    except RuntimeError:
        bot_id = None

    if bot_id is not None:
        return bot_id

    bot_user = await context.bot.get_me()
    return bot_user.id


async def new_chat_members(update: Update, context: CallbackContext) -> None:
    """Handle new chat members by sending them a verification message after a delay."""
    logging.debug(f"context.user_data at function start: {context.user_data}")
    remember_chat(update.effective_chat, "new_chat_members")
    if update.message.text and update.message.text.startswith("/new"):
        logging.info("This is a command, not a new chat member.")
        member = update.message.from_user
        await send_verification_message(update, context, member)
    else:
        new_members = update.message.new_chat_members
        logging.info(
            f"New chat members update in {format_chat(update.effective_chat)}: "
            f"{[format_user(member) for member in new_members]}"
        )
        bot_id = await get_current_bot_id(context)
        new_members = [member for member in new_members if member.id != bot_id]
        if not new_members:
            logging.info("Ignoring new chat members update because it only contains the bot itself.")
            return
        for member in new_members:
            await mute_user(update, context, member.id)
        await asyncio.sleep(VERIFICATION_DELAY_SECONDS)
        for member in new_members:
            await send_verification_message(update, context, member, restart_existing=False, mute_pending=False)
    logging.debug(f"context.user_data at function end: {context.user_data}")


async def send_verification_message(
    update: Update,
    context: CallbackContext,
    user,
    restart_existing: bool = True,
    mute_pending: bool = True,
) -> None:
    """Send a verification message to the user with a set of answers to choose from."""
    logging.debug(f"context.user_data at function start: {context.user_data}")
    chat_id = update.effective_chat.id
    bot_id = await get_current_bot_id(context)
    if user.id == bot_id:
        logging.info("Skipping verification for the bot itself.")
        return

    session_key = get_session_key(chat_id, user.id)
    task_key = get_timeout_task_key(chat_id, user.id)

    if verification_sessions.get(session_key) and not restart_existing:
        logging.info(f"Verification session already exists for {format_user(user)} in chat_id={chat_id}.")
        return

    existing_task = jobs_dict.pop(task_key, None)
    if existing_task:
        existing_task.cancel()
    clear_verification_session(chat_id, user.id)
    if mute_pending:
        await mute_user(update, context, user.id)

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
        "messages": [sent_message],
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
                f"{user.mention_html()}, {remaining_time} seconds left to answer the verification question."
            )
            reminder_message = await context.bot.send_message(chat_id=chat_id, text=reminder_text, parse_mode="HTML")
            add_session_message(chat_id, user.id, reminder_message)
            logging.info(f"User {user.id} has {remaining_time} seconds left to respond.")
            half_time_sent = True
        await asyncio.sleep(step)
        remaining_time -= step

    session = verification_sessions.get(session_key)
    if session and not session.get("verified", False):
        logging.info(f"User {user.id} did not respond in time. Kicking.")
        timeout_message = await context.bot.send_message(
            chat_id=chat_id,
            text=f"{user.mention_html()} did not respond in time. Kicking.",
            parse_mode="HTML",
        )
        add_session_message(chat_id, user.id, timeout_message)
        await kick_user(update, context, user.id)
        await cleanup_verification_session(chat_id, user.id)

    jobs_dict.pop(task_key, None)
    logging.debug(f"context.user_data at function end: {context.user_data}")


async def kick_user(update: Update, context: CallbackContext, user_id: int) -> None:
    """Kick the user out of the chat and unban them after a delay."""
    bot_id = await get_current_bot_id(context)
    if user_id == bot_id:
        logging.warning("Refusing to kick the bot itself.")
        return

    chat_id = update.effective_chat.id
    await context.bot.ban_chat_member(chat_id=chat_id, user_id=user_id)
    logging.info(f"User {user_id} has been kicked.")
    asyncio.create_task(unban_user_later(context, chat_id, user_id))


async def unban_user_later(context: CallbackContext, chat_id: int, user_id: int) -> None:
    await asyncio.sleep(unban_delay_seconds)
    await context.bot.unban_chat_member(chat_id=chat_id, user_id=user_id)
    logging.info(f"User {user_id} has been unbanned.")


async def mute_user(update: Update, context: CallbackContext, user_id: int) -> None:
    """Mute a user while they are waiting for verification."""
    bot_id = await get_current_bot_id(context)
    if user_id == bot_id:
        logging.warning("Refusing to mute the bot itself.")
        return

    try:
        await context.bot.restrict_chat_member(
            chat_id=update.effective_chat.id,
            user_id=user_id,
            permissions=MUTED_PERMISSIONS,
        )
        logging.info(f"User {user_id} has been muted until verification.")
    except BadRequest as exc:
        logging.warning(f"Failed to mute user {user_id}: {exc}")


async def unmute_user(update: Update, context: CallbackContext, user_id: int) -> None:
    """Restore basic send permissions after successful verification."""
    bot_id = await get_current_bot_id(context)
    if user_id == bot_id:
        logging.warning("Refusing to unmute the bot itself.")
        return

    try:
        await context.bot.restrict_chat_member(
            chat_id=update.effective_chat.id,
            user_id=user_id,
            permissions=UNMUTED_PERMISSIONS,
        )
        logging.info(f"User {user_id} has been unmuted after verification.")
    except BadRequest as exc:
        logging.warning(f"Failed to unmute user {user_id}: {exc}")


async def message_delete(message) -> None:
    """Delete the message if it exists."""
    try:
        await message.delete()
    except BadRequest:
        logging.info("Message already deleted or not found.")


async def cleanup_verification_session(chat_id: int, user_id: int) -> None:
    session = verification_sessions.get(get_session_key(chat_id, user_id))
    if session is None:
        return

    for message in session.get("messages", []):
        await message_delete(message)
    clear_verification_session(chat_id, user_id)


async def handle_answer(update: Update, context: CallbackContext) -> None:
    """Handle the user's answer to the verification question."""
    logging.debug(f"context.user_data at function start: {context.user_data}")
    remember_chat(update.effective_chat, "callback_query")
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
        success_message = await context.bot.send_message(
            chat_id=chat_id,
            text=f"{query.from_user.mention_html()} provided the correct answer.",
            parse_mode="HTML",
        )
        add_session_message(chat_id, user_id, success_message)
        logging.info(f"User {user_id} provided the correct answer.")
        session["verified"] = True
        await unmute_user(update, context, user_id)
    else:
        logging.info(f"User {user_id} provided an incorrect answer.")
        incorrect_message = await context.bot.send_message(
            chat_id=chat_id,
            text=f"{query.from_user.mention_html()} provided an incorrect answer. Kicking.",
            parse_mode="HTML",
        )
        add_session_message(chat_id, user_id, incorrect_message)
        await kick_user(update, context, user_id)

    await query.answer()
    logging.info(f"Cleaning verification messages for user {user_id}.")
    await cleanup_verification_session(chat_id, user_id)
    logging.info(f"Deleting timeout task for user {user_id}.")

    task = jobs_dict.pop(task_key, None)
    if task is None:
        logging.warning(f"No timeout task found for user {user_id} in jobs_dict.")
    if task:
        task.cancel()
    logging.debug(f"context.user_data at function end: {context.user_data}")


async def ping_command(update: Update, context: CallbackContext) -> None:
    """Respond to the /ping command with 'pong'."""
    remember_chat(update.effective_chat, "ping")
    await update.message.reply_html("pong", disable_web_page_preview=True)


async def log_update(update: Update, context: CallbackContext) -> None:
    """Log a concise description of every update the bot receives."""
    remember_chat(update.effective_chat, "update")

    update_types = []
    for attr in (
        "message",
        "edited_message",
        "callback_query",
        "my_chat_member",
        "chat_member",
        "channel_post",
    ):
        if getattr(update, attr, None) is not None:
            update_types.append(attr)

    user = update.effective_user
    logging.debug(
        f"Received update id={update.update_id}, types={update_types}, "
        f"{format_chat(update.effective_chat)}, {format_user(user)}"
    )


async def log_my_chat_member(update: Update, context: CallbackContext) -> None:
    """Log when the bot is added, removed, promoted, or demoted in a chat."""
    member_update = update.my_chat_member
    remember_chat(member_update.chat, "my_chat_member")
    logging.debug(
        f"Bot chat status changed in {format_chat(member_update.chat)} by "
        f"{format_user(member_update.from_user)}: "
        f"old=({format_chat_member(member_update.old_chat_member)}), "
        f"new=({format_chat_member(member_update.new_chat_member)})"
    )


async def log_chat_member(update: Update, context: CallbackContext) -> None:
    """Log chat member status changes visible to the bot."""
    member_update = update.chat_member
    remember_chat(member_update.chat, "chat_member")
    old_status = member_update.old_chat_member.status
    new_status = member_update.new_chat_member.status
    user = member_update.new_chat_member.user
    logging.debug(
        f"Chat member status changed in {format_chat(member_update.chat)} by "
        f"{format_user(member_update.from_user)}: "
        f"old=({format_chat_member(member_update.old_chat_member)}), "
        f"new=({format_chat_member(member_update.new_chat_member)})"
    )

    if old_status in LEFT_CHAT_STATUSES and new_status in JOINED_CHAT_STATUSES:
        logging.info(
            f"Treating chat_member status change as a new join for {format_user(user)} "
            f"in {format_chat(member_update.chat)}."
        )
        await mute_user(update, context, user.id)
        await asyncio.sleep(VERIFICATION_DELAY_SECONDS)
        await send_verification_message(update, context, user, restart_existing=False, mute_pending=False)


async def log_bot_identity(application: Application) -> None:
    bot_user = await application.bot.get_me()
    logging.info(f"Bot started as {format_user(bot_user)}")


def main() -> None:
    """Start the bot and add command handlers."""
    application = Application.builder().token(telegram_api_token).post_init(log_bot_identity).build()

    application.add_handler(TypeHandler(Update, log_update), group=-1)
    application.add_handler(ChatMemberHandler(log_my_chat_member, ChatMemberHandler.MY_CHAT_MEMBER))
    application.add_handler(ChatMemberHandler(log_chat_member, ChatMemberHandler.CHAT_MEMBER))
    application.add_handler(CommandHandler("ping", ping_command))
    application.add_handler(CommandHandler("new", new_chat_members))
    application.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, new_chat_members))
    application.add_handler(CallbackQueryHandler(handle_answer, pattern=r"^verify:\d+:[^:]+$"))

    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
