import os
import re
import logging
import asyncio
import threading
from io import BytesIO
from urllib.parse import urlparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import requests
from telegram import Update
from telegram.ext import (
    Application,
    MessageHandler,
    ContextTypes,
    filters,
)

# =========================
# CONFIGURATION
# =========================

TOKEN = os.environ.get("BOT_TOKEN", "").strip()
PORT = int(os.environ.get("PORT", "10000"))
MAX_SIZE = 10 * 1024 * 1024  # 10 MB

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger(__name__)


# =========================
# RENDER WEB SERVICE PORT
# =========================

class HealthHandler(BaseHTTPRequestHandler):

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"Image Downloader Bot is running!")

    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()

    def log_message(self, format, *args):
        pass


def start_web_server():
    server = ThreadingHTTPServer(
        ("0.0.0.0", PORT),
        HealthHandler,
    )

    logger.info("Web server listening on port %s", PORT)

    server.serve_forever()


# =========================
# DOWNLOAD IMAGE FROM URL
# =========================

def download_image(url):
    try:
        parsed = urlparse(url)

        if (
            parsed.scheme not in ("http", "https")
            or not parsed.hostname
            or parsed.username
            or parsed.password
        ):
            return None

        headers = {
            "User-Agent": "Mozilla/5.0",
            "Accept": "image/*",
        }

        with requests.get(
            url,
            headers=headers,
            timeout=(10, 30),
            stream=True,
            allow_redirects=True,
        ) as response:

            response.raise_for_status()

            content_type = response.headers.get(
                "Content-Type", ""
            ).lower()

            if not content_type.startswith("image/"):
                return None

            content_length = response.headers.get("Content-Length")

            if content_length:
                try:
                    if int(content_length) > MAX_SIZE:
                        return None
                except ValueError:
                    pass

            image_buffer = BytesIO()
            total_size = 0

            for chunk in response.iter_content(
                chunk_size=65536
            ):
                if not chunk:
                    continue

                total_size += len(chunk)

                if total_size > MAX_SIZE:
                    return None

                image_buffer.write(chunk)

            image_data = image_buffer.getvalue()

            if not image_data:
                return None

            # Accept common image formats only.
            is_jpeg = image_data.startswith(b"\xff\xd8\xff")

            is_png = image_data.startswith(
                b"\x89PNG\r\n\x1a\n"
            )

            is_gif = image_data.startswith(
                (b"GIF87a", b"GIF89a")
            )

            is_webp = (
                len(image_data) >= 12
                and image_data[:4] == b"RIFF"
                and image_data[8:12] == b"WEBP"
            )

            if not (is_jpeg or is_png or is_gif or is_webp):
                return None

            return image_data

    except (
        requests.RequestException,
        ValueError,
        OSError,
    ) as error:
        logger.warning("Image download failed: %s", error)
        return None


# =========================
# TELEGRAM MESSAGE HANDLER
# =========================

async def handle_link(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    message = update.effective_message

    if not message or not message.text:
        return

    text = message.text.strip()

    # Find a URL in the user's message.
    match = re.search(
        r"https?://[^\s<>]+",
        text,
    )

    if not match:
        return

    url = match.group(0).rstrip(".,!?)']\"")

    # Download without blocking the Telegram event loop.
    image_data = await asyncio.to_thread(
        download_image,
        url,
    )

    # Do not send documents or failure messages.
    if image_data is None:
        logger.info("No supported image found at the URL.")
        return

    try:
        photo = BytesIO(image_data)
        photo.name = "image.jpg"

        await message.reply_photo(
            photo=photo,
        )

        logger.info("Image sent successfully.")

    except Exception as error:
        logger.warning(
            "Telegram photo upload failed: %s",
            error,
        )


# =========================
# START BOT
# =========================

def main():
    if not TOKEN:
        raise RuntimeError(
            "BOT_TOKEN is missing from Render Environment Variables."
        )

    # Start the HTTP server required by Render Web Service.
    web_thread = threading.Thread(
        target=start_web_server,
        daemon=True,
    )

    web_thread.start()

    # Start Telegram bot polling.
    app = Application.builder().token(TOKEN).build()

    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            handle_link,
        )
    )

    logger.info("Image bot started.")

    app.run_polling(
        drop_pending_updates=True,
    )


if __name__ == "__main__":
    main()
