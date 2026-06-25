---
title: picBot
sdk: docker
app_port: 7860
---

# picBot

Minimal Telegram bot that turns an incoming image into a Telegram custom emoji pack.

## Setup

1. Create a bot with `@BotFather` and put the token into `.env`:

   ```bash
   cp .env.example .env
   ```

2. Install dependencies:

   ```bash
   python3 -m venv .venv
   . .venv/bin/activate
   pip install -r requirements.txt
   ```

3. Run:

   ```bash
python bot.py
```

Send the bot a photo or an image file. It will create a custom emoji set with title `/` and send back a link to add it.

For Hugging Face deployment, see [DEPLOY.md](DEPLOY.md).

## Notes

- Custom emoji sets are a Telegram Premium feature on the client side.
- Telegram requires every sticker set short name to be unique and to end with `_by_<bot_username>`. The visible title is still `/`.
- Telegram does not allow bots to send custom emoji stickers as normal sticker messages, so the bot sends the created pack link instead.
- Default slicing chooses a grid from the image aspect ratio, aiming around 8 columns and capped by `MAX_EMOJIS`.
- Telegram custom emoji images must be exactly `100x100`; the bot prepares every tile in that format.
- Before slicing, the bot trims flat/transparent borders and fits the useful image area onto one mosaic canvas, so tiles connect cleanly.
