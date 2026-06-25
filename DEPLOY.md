# Deploy

This bot can run locally with Telegram polling or on Hugging Face Spaces with Telegram webhooks.

## Hugging Face Space variables

Use a Docker Space and add these secrets/variables:

```text
BOT_TOKEN=your_botfather_token
WEBHOOK_URL=https://username-spacename.hf.space
TELEGRAM_API_URL=https://your-worker.workers.dev/bot{0}/{1}
```

Optional:

```text
EMOJI_COLUMNS=8
MAX_EMOJIS=50
PACK_TITLE=/
SEND_PACK_LINK=1
PADDING_RATIO=0.04
TRIM_TOLERANCE=18
SHARPEN_AMOUNT=1.25
```

Image-specific options can be sent in a caption, for example:

```text
w=9 b=white t=35
```

## Cloudflare Worker

Deploy `cloudflare-worker.js` as a Worker. Its URL becomes the prefix for `TELEGRAM_API_URL`.

Example:

```text
https://example.workers.dev/bot{0}/{1}
```

## Keepalive

On the free Hugging Face tier, configure a cron-job.org ping every 5 minutes to:

```text
https://username-spacename.hf.space
```
