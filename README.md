# Telegram Verification Bot

## Overview
This bot manages new chat members in Telegram groups by challenging them with a verification question. Users must respond correctly within a set time limit to stay in the group.


## Setup and Run
**Put correct variables into secrets.env file**:
   - `TELEGRAM_BOT_TOKEN`: Your Telegram bot token.
   - `QUESTION`, `GOOD_ANSWER`, `BAD_ANSWER`: Set the verification question and answers.
   - `TIMEOUT`: Time limit for new members to respond (in seconds, default: 180).  

**Execute**

```bash
docker-compose build && docker-compose up
```

