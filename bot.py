from __future__ import annotations

import io
import json
import logging
import math
import os
import re
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import requests
from dotenv import load_dotenv
from PIL import Image, ImageChops, ImageFilter, ImageOps
from requests import Response


load_dotenv()

BOT_TOKEN = (os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("BOT_TOKEN") or "").strip()
TELEGRAM_API_URL = os.getenv("TELEGRAM_API_URL", "").strip()
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "").strip()

EMOJI_COLUMNS = max(1, int(os.getenv("EMOJI_COLUMNS", "8")))
MAX_EMOJIS = min(50, max(1, int(os.getenv("MAX_EMOJIS", "50"))))
PACK_TITLE = os.getenv("PACK_TITLE", "/")[:64] or "/"
SEND_PACK_LINK = os.getenv("SEND_PACK_LINK", "1") != "0"
PADDING_RATIO = min(0.25, max(0.0, float(os.getenv("PADDING_RATIO", "0.04"))))
TRIM_TOLERANCE = max(0, int(os.getenv("TRIM_TOLERANCE", "18")))
SHARPEN_AMOUNT = max(0.0, float(os.getenv("SHARPEN_AMOUNT", "1.25")))

TILE_SIZE = 100
POLL_TIMEOUT = 30
SERVER_PORT = int(os.getenv("PORT", "7860"))
PLACEHOLDER_EMOJI = "⬛"
ENTITY_PLACEHOLDER = "⬛"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("picbot")
SESSION = requests.Session()


@dataclass(frozen=True)
class BotIdentity:
    username: str


class TelegramError(RuntimeError):
    pass


def telegram_api_url(method: str) -> str:
    if TELEGRAM_API_URL:
        return TELEGRAM_API_URL.format(BOT_TOKEN, method)
    return f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"


def telegram_file_url(file_path: str) -> str:
    if TELEGRAM_API_URL:
        worker_base = TELEGRAM_API_URL.split("/bot{0}/{1}", 1)[0].rstrip("/")
        return f"{worker_base}/file/bot{BOT_TOKEN}/{file_path}"
    return f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}"


def request_with_retries(method: str, url: str, **kwargs: Any) -> Response:
    last_error: Exception | None = None
    for attempt in range(4):
        try:
            return SESSION.request(method, url, timeout=120, **kwargs)
        except (requests.ConnectionError, requests.Timeout, requests.exceptions.SSLError) as exc:
            last_error = exc
            if attempt == 3:
                break
            delay = 1.5 * (attempt + 1)
            log.warning("telegram request failed, retrying in %.1fs: %s", delay, exc)
            time.sleep(delay)
    raise last_error or TelegramError("telegram request failed")


def api(method: str, *, data: dict[str, Any] | None = None, files: dict[str, Any] | None = None) -> Any:
    response = request_with_retries("POST", telegram_api_url(method), data=data, files=files)
    try:
        payload = response.json()
    except ValueError as exc:
        raise TelegramError(f"{method}: non-JSON response {response.status_code}") from exc

    if not payload.get("ok"):
        description = payload.get("description", "unknown Telegram error")
        raise TelegramError(f"{method}: {description}")

    return payload["result"]


def get_json(method: str, params: dict[str, Any] | None = None) -> Any:
    response = request_with_retries("GET", telegram_api_url(method), params=params)
    payload = response.json()
    if not payload.get("ok"):
        raise TelegramError(f"{method}: {payload.get('description', 'unknown Telegram error')}")
    return payload["result"]


def detect_image_message(message: dict[str, Any]) -> tuple[str, str] | None:
    if photos := message.get("photo"):
        return photos[-1]["file_id"], "photo.jpg"

    document = message.get("document")
    if document and str(document.get("mime_type", "")).startswith("image/"):
        return document["file_id"], document.get("file_name") or "image"

    return None


def download_file(file_id: str) -> bytes:
    file_info = get_json("getFile", {"file_id": file_id})
    file_path = file_info["file_path"]
    response = request_with_retries("GET", telegram_file_url(file_path))
    response.raise_for_status()
    return response.content


def grid_for_image(width: int, height: int) -> tuple[int, int]:
    aspect = width / max(height, 1)
    best: tuple[float, int, int] | None = None

    max_side = min(10, MAX_EMOJIS)
    for columns in range(1, max_side + 1):
        for rows in range(1, max_side + 1):
            count = columns * rows
            if count > MAX_EMOJIS:
                continue

            grid_aspect = columns / rows
            aspect_error = abs(math.log(grid_aspect / aspect))
            density_bonus = count / MAX_EMOJIS
            too_tiny_penalty = 0.35 if count < 9 else 0.0
            preferred_width_penalty = abs(columns - EMOJI_COLUMNS) * 0.035
            score = aspect_error + preferred_width_penalty + too_tiny_penalty - density_bonus * 0.18

            if best is None or score < best[0]:
                best = (score, columns, rows)

    if best is None:
        return 1, 1
    return best[1], best[2]


def trim_uniform_border(image: Image.Image) -> Image.Image:
    rgba = image.convert("RGBA")
    alpha_bbox = rgba.getchannel("A").point(lambda value: 255 if value > 8 else 0).getbbox()
    if alpha_bbox:
        rgba = rgba.crop(alpha_bbox)

    width, height = rgba.size
    samples = [
        rgba.getpixel((0, 0)),
        rgba.getpixel((width - 1, 0)),
        rgba.getpixel((0, height - 1)),
        rgba.getpixel((width - 1, height - 1)),
    ]
    background = tuple(round(sum(pixel[channel] for pixel in samples) / len(samples)) for channel in range(4))

    background_image = Image.new("RGBA", rgba.size, background)
    diff = ImageChops.difference(rgba, background_image).convert("L")
    mask = diff.point(lambda value: 255 if value > TRIM_TOLERANCE else 0)
    bbox = mask.getbbox()
    if bbox is None:
        return rgba

    left, upper, right, lower = bbox
    pad = round(max(right - left, lower - upper) * PADDING_RATIO)
    left = max(0, left - pad)
    upper = max(0, upper - pad)
    right = min(width, right + pad)
    lower = min(height, lower + pad)
    return rgba.crop((left, upper, right, lower))


def prepare_source_image(image: Image.Image) -> Image.Image:
    image = trim_uniform_border(image)
    alpha = image.getchannel("A")
    rgb = ImageOps.autocontrast(image.convert("RGB"), preserve_tone=True)
    image = Image.merge("RGBA", (*rgb.split(), alpha))
    if SHARPEN_AMOUNT:
        image = image.filter(ImageFilter.UnsharpMask(radius=1.1, percent=int(110 * SHARPEN_AMOUNT), threshold=3))
    return image


def encode_tile(tile: Image.Image) -> bytes:
    output = io.BytesIO()
    tile.save(output, format="PNG", optimize=True)
    return output.getvalue()


def slice_image(image_bytes: bytes) -> tuple[list[bytes], int, int]:
    with Image.open(io.BytesIO(image_bytes)) as source:
        image = ImageOps.exif_transpose(source).convert("RGBA")

    image = prepare_source_image(image)
    columns, rows = grid_for_image(image.width, image.height)
    mosaic = ImageOps.fit(
        image,
        (columns * TILE_SIZE, rows * TILE_SIZE),
        method=Image.Resampling.LANCZOS,
        centering=(0.5, 0.5),
    )

    tiles: list[bytes] = []
    for row in range(rows):
        for col in range(columns):
            left = col * TILE_SIZE
            upper = row * TILE_SIZE
            right = left + TILE_SIZE
            lower = upper + TILE_SIZE
            tiles.append(encode_tile(mosaic.crop((left, upper, right, lower))))

    return tiles, columns, rows


def sanitize_name_piece(value: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9_]", "_", value)
    value = re.sub(r"_+", "_", value).strip("_")
    return value or "u"


def make_pack_name(user_id: int, bot_username: str) -> str:
    suffix = f"_by_{sanitize_name_piece(bot_username).lower()}"
    seed = f"slash_{sanitize_name_piece(str(user_id))}_{int(time.time() * 1000)}"
    max_seed_length = 64 - len(suffix)
    return f"{seed[:max_seed_length].rstrip('_')}{suffix}"


def create_custom_emoji_set(user_id: int, bot: BotIdentity, tiles: list[bytes]) -> str:
    pack_name = make_pack_name(user_id, bot.username)
    stickers: list[dict[str, Any]] = []
    files: dict[str, Any] = {}

    for index, tile in enumerate(tiles):
        attach_name = f"tile_{index}"
        stickers.append(
            {
                "sticker": f"attach://{attach_name}",
                "format": "static",
                "emoji_list": [PLACEHOLDER_EMOJI],
            }
        )
        files[attach_name] = (f"{attach_name}.png", io.BytesIO(tile), "image/png")

    api(
        "createNewStickerSet",
        data={
            "user_id": str(user_id),
            "name": pack_name,
            "title": PACK_TITLE,
            "sticker_type": "custom_emoji",
            "stickers": json.dumps(stickers, ensure_ascii=False),
        },
        files=files,
    )
    return pack_name


def send_pack_link(chat_id: int, pack_name: str) -> None:
    api(
        "sendMessage",
        data={
            "chat_id": str(chat_id),
            "text": f"https://t.me/addemoji/{pack_name}",
            "disable_web_page_preview": "true",
        },
    )


def send_custom_emoji_art(chat_id: int, pack_name: str, columns: int, rows: int) -> None:
    sticker_set = api("getStickerSet", data={"name": pack_name})
    stickers = sticker_set.get("stickers") or []

    text_rows: list[str] = []
    entities: list[dict[str, Any]] = []
    offset = 0

    for row in range(rows):
        row_text = ENTITY_PLACEHOLDER * columns
        text_rows.append(row_text)

        for col in range(columns):
            sticker_index = row * columns + col
            if sticker_index >= len(stickers):
                break

            custom_emoji_id = stickers[sticker_index].get("custom_emoji_id")
            if not custom_emoji_id:
                continue

            entities.append(
                {
                    "type": "custom_emoji",
                    "offset": offset + col,
                    "length": 1,
                    "custom_emoji_id": custom_emoji_id,
                }
            )

        offset += len(row_text) + 1

    api(
        "sendMessage",
        data={
            "chat_id": str(chat_id),
            "text": "\n".join(text_rows),
            "entities": json.dumps(entities),
            "disable_web_page_preview": "true",
        },
    )


def handle_message(message: dict[str, Any], bot: BotIdentity) -> None:
    image = detect_image_message(message)
    if image is None:
        return

    chat_id = message["chat"]["id"]
    user_id = message.get("from", {}).get("id")
    if user_id is None:
        return

    file_id, file_name = image
    log.info("processing image from user=%s chat=%s file=%s", user_id, chat_id, file_name)

    image_bytes = download_file(file_id)
    tiles, columns, rows = slice_image(image_bytes)
    pack_name = create_custom_emoji_set(user_id, bot, tiles)
    log.info("created custom emoji set %s with %s tiles", pack_name, len(tiles))

    if SEND_PACK_LINK:
        try:
            send_custom_emoji_art(chat_id, pack_name, columns, rows)
        except TelegramError:
            log.exception("failed to send custom emoji art, sending pack link instead")
            send_pack_link(chat_id, pack_name)


def process_update(update: dict[str, Any], bot: BotIdentity) -> None:
    message = update.get("message")
    if not message:
        return

    try:
        handle_message(message, bot)
    except Exception:
        log.exception("failed to process message")


def make_webhook_handler(bot: BotIdentity) -> type[BaseHTTPRequestHandler]:
    class BotHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            log.info("ping received")
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"OK")

        def do_POST(self) -> None:
            content_length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(content_length)
            try:
                update = json.loads(body.decode("utf-8"))
                process_update(update, bot)
            except Exception:
                log.exception("webhook update error")

            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"OK")

        def log_message(self, *args: Any) -> None:
            return

    return BotHandler


def run_polling(bot: BotIdentity) -> None:
    try:
        api("deleteWebhook", data={"drop_pending_updates": "false"})
    except TelegramError:
        log.exception("failed to delete webhook before polling")

    offset = 0
    while True:
        try:
            updates = get_json(
                "getUpdates",
                {"offset": offset, "timeout": POLL_TIMEOUT, "allowed_updates": json.dumps(["message"])},
            )
            for update in updates:
                offset = update["update_id"] + 1
                process_update(update, bot)
        except KeyboardInterrupt:
            log.info("stopped")
            return
        except Exception:
            log.exception("polling failed")
            time.sleep(3)


def run_webhook(bot: BotIdentity) -> None:
    api("deleteWebhook", data={"drop_pending_updates": "false"})
    time.sleep(1)
    api(
        "setWebhook",
        data={
            "url": WEBHOOK_URL,
            "allowed_updates": json.dumps(["message"]),
            "drop_pending_updates": "false",
        },
    )
    log.info("webhook mode: %s", WEBHOOK_URL)
    log.info("webhook set")
    HTTPServer(("0.0.0.0", SERVER_PORT), make_webhook_handler(bot)).serve_forever()


def run() -> None:
    if not BOT_TOKEN:
        raise SystemExit("TELEGRAM_BOT_TOKEN or BOT_TOKEN is required.")
    if WEBHOOK_URL and not TELEGRAM_API_URL:
        raise SystemExit("TELEGRAM_API_URL is required in webhook mode. Use the Cloudflare Worker URL: https://your-worker.workers.dev/bot{0}/{1}")

    me = get_json("getMe")
    bot = BotIdentity(username=me["username"])
    log.info("started as @%s", bot.username)

    if WEBHOOK_URL:
        run_webhook(bot)
    else:
        run_polling(bot)


if __name__ == "__main__":
    run()
