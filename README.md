# Telegram Verification Bot

## Overview
This bot manages new chat members in Telegram groups by challenging them with a verification question.

When a user joins, the bot:
- detects both Telegram join event types: `new_chat_members` messages and `chat_member` status changes;
- immediately mutes the user to prevent join spam;
- waits briefly, then sends the verification question;
- unmutes the user after the correct answer;
- kicks and unbans the user if they fail verification or do not answer before the timeout.

Verification prompts, reminders, and result messages are deleted after the verification flow finishes to reduce chat noise.

## Setup and Run
**Put correct variables into secrets.env file**:
   - `TELEGRAM_BOT_TOKEN`: Your Telegram bot token.
   - `QUESTION`, `GOOD_ANSWER`, `BAD_ANSWER`: Set the verification question and answers.
   - `TIMEOUT`: Time limit for new members to respond (in seconds, default: 180).
   - `UNBAN_DELAY_SECONDS`: How long to keep a kicked user banned before unbanning them (in seconds, default: 5).

**Required bot permissions in each group**:
   - Restrict members / Ban users: required for muting, kicking, and unbanning users.
   - Delete messages: recommended so the bot can clean up verification messages.

**Execute**

```bash
docker compose up -d --build
```

