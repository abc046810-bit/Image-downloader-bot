import os
import re
import logging
import asyncio
from io import BytesIO
from urllib.parse import urlparse

import requests
from telegram import Update
from telegram.ext import (
    Application,
    MessageHandler,
    ContextTypes,
    filters,
)

TOKEN = os.environ.get("BOT_TOKEN", "").strip()
MAX_SIZE = 10 * 1024 * 1024  # 10 MB

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger(__name__)


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

            length = response.headers.get("Content-Length")

            if length and int(length) > MAX_SIZE:
                return None

            data = BytesIO()
            total = 0

            for chunk in response.iter_content(65536):
                if not chunk:
                    continue

                total += len(chunk)

                if total > MAX_SIZE:
                    return None

                data.write(chunk)

            image_data = data.getvalue()

            if not image_data:
                return None

            # Telegram photo uploads require a supported image format.
            if not (
                image_data.startswith(b"\xff\xd8\xff")
                or image_data.startswith(b"\x89PNG\r\n\x1a\n")
                or (
                    image_data.startswith(b"GIF87a")
                    or image_data.startswith(b"GIF89a")
                )
                or (
                    len(image_data) >= 12
                    and image_data[:4] == b"RIFF"
                    and image_data[8:12] == b"WEBP"
                )
            ):
                return None

            return image_data

    except (requests.RequestException, ValueError, OSError) as error:
        logger.warning("Image download failed: %s", error)
        return None


async def handle_link(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    message = update.effective_message

    if not message or not message.text:
        return

    text = message.text.strip()

    # Accept a direct URL, including one surrounded by other text.
    match = re.search(r"https?://[^\s<>]+", text)

    if not match:
        return

    url = match.group(0).rstrip(".,!?)']\"")

    image_data = await asyncio.to_thread(download_image, url)

    # No documents and no text messages for failed downloads.
    if image_data is None:
        return

    try:
        await message.reply_photo(
            photo=BytesIO(image_data),
            filename="image.jpg",
        )
    except Exception as error:
        logger.warning("Telegram photo upload failed: %s", error)


def main():
    if not TOKEN:
        raise RuntimeError(
            "BOT_TOKEN environment variable is missing."
        )

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
