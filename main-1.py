import logging
import os
import re
import socket
import ipaddress
import threading
from urllib.parse import urljoin, urlparse

import requests
from flask import Flask
from telegram import Update
from telegram.error import BadRequest, TelegramError
from telegram.ext import Application, ContextTypes, MessageHandler, filters
from telegram.request import HTTPXRequest

# Required Render environment variable: BOT_TOKEN
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
PORT = int(os.getenv("PORT", "10000"))

# Keep downloads bounded for Render's free instance and Telegram limits.
MAX_BYTES = 18 * 1024 * 1024
TIMEOUT = (12, 35)
USER_AGENT = "Mozilla/5.0 (compatible; ImageDownloaderBot/1.0)"
IMAGE_TYPES = {
    "image/jpeg", "image/png", "image/webp", "image/gif",
    "image/bmp", "image/tiff", "image/avif"
}
URL_RE = re.compile(r'https?://[^\s<>"\'\]]+', re.IGNORECASE)

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO
)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("image-downloader-bot")

# Minimal health endpoint required by Render Web Service.
web = Flask(__name__)

@web.get("/")
def home():
    return "Image Downloader Bot is running.", 200

@web.get("/health")
def health():
    return {"status": "ok"}, 200


def _public_http_url(url: str) -> bool:
    """Allow only public HTTP/HTTPS hosts (avoid localhost/private-network URLs)."""
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            return False
        if parsed.username or parsed.password:
            return False
        host = parsed.hostname
        try:
            ip = ipaddress.ip_address(host)
            return ip.is_global
        except ValueError:
            pass
        # Resolve host and reject it if it resolves to a non-public address.
        infos = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80))
        return bool(infos) and all(ipaddress.ip_address(info[4][0]).is_global for info in infos)
    except Exception:
        return False


def _clean_url(url: str) -> str:
    return url.rstrip(".,);!?")


def _extract_image_url(page_url: str, html: str) -> str | None:
    """Find common social-preview image tags and then ordinary image elements."""
    from html.parser import HTMLParser

    class ImageParser(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.meta = []
            self.images = []

        def handle_starttag(self, tag, attrs):
            a = {k.lower(): (v or "") for k, v in attrs}
            if tag.lower() == "meta":
                key = (a.get("property") or a.get("name") or "").lower()
                if key in ("og:image", "og:image:url", "twitter:image", "twitter:image:src"):
                    value = a.get("content", "")
                    if value:
                        self.meta.append(value)
            elif tag.lower() == "img":
                value = a.get("src") or a.get("data-src") or a.get("data-original")
                if value:
                    self.images.append(value)

    parser = ImageParser()
    try:
        parser.feed(html)
    except Exception:
        return None

    for candidate in parser.meta + parser.images:
        absolute = urljoin(page_url, candidate.strip())
        if _public_http_url(absolute):
            return absolute
    return None


def _download_image(url: str) -> tuple[bytes, str, str]:
    """Return (bytes, content-type, filename); supports direct links and image-host pages."""
    current_url = url
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    for _ in range(4):
        if not _public_http_url(current_url):
            raise ValueError("This link is not a valid public HTTP/HTTPS URL.")
        with session.get(
            current_url, timeout=TIMEOUT, stream=True, allow_redirects=True
        ) as response:
            response.raise_for_status()
            final_url = response.url
            if not _public_http_url(final_url):
                raise ValueError("The link redirected to an unsupported address.")

            content_type = response.headers.get("Content-Type", "").split(";")[0].strip().lower()
            if content_type in IMAGE_TYPES or content_type.startswith("image/"):
                length = response.headers.get("Content-Length")
                if length and int(length) > MAX_BYTES:
                    raise ValueError("Image is too large (maximum 18 MB).")
                chunks = []
                total = 0
                for chunk in response.iter_content(chunk_size=64 * 1024):
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > MAX_BYTES:
                        raise ValueError("Image is too large (maximum 18 MB).")
                    chunks.append(chunk)
                data = b"".join(chunks)
                if not data:
                    raise ValueError("The image link returned an empty response.")
                path_name = os.path.basename(urlparse(final_url).path) or "image"
                if "." not in path_name:
                    ext = {
                        "image/jpeg": ".jpg", "image/png": ".png",
                        "image/webp": ".webp", "image/gif": ".gif",
                        "image/bmp": ".bmp", "image/tiff": ".tiff",
                        "image/avif": ".avif"
                    }.get(content_type, ".img")
                    path_name += ext
                return data, content_type, path_name[:100]

            # Image-hosting pages (e.g. pages with an og:image preview).
            if "text/html" in content_type or not content_type:
                html_chunks = []
                total = 0
                for chunk in response.iter_content(chunk_size=32 * 1024):
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > 2 * 1024 * 1024:
                        break
                    html_chunks.append(chunk)
                html = b"".join(html_chunks).decode(
                    response.encoding or "utf-8", errors="replace"
                )
                next_url = _extract_image_url(final_url, html)
                if not next_url or next_url == current_url:
                    raise ValueError("No image was found on this page. Please send a direct image link.")
                current_url = next_url
                continue

            raise ValueError("This link does not appear to contain an image.")
    raise ValueError("Could not find the image after following the page link.")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if not message or not message.text:
        return

    urls = [_clean_url(x) for x in URL_RE.findall(message.text)]
    if not urls:
        return

    # Process links one by one; no extra status messages are sent.
    for url in urls:
        try:
            data, content_type, filename = _download_image(url)
            from io import BytesIO
            image_file = BytesIO(data)
            image_file.name = filename

            # Send as a Telegram photo for common photo formats. For formats that
            # Telegram cannot display as a photo, send the original bytes unchanged.
            if content_type in ("image/jpeg", "image/png", "image/webp"):
                try:
                    await message.reply_photo(
                        photo=image_file,
                        read_timeout=60,
                        write_timeout=90,
                        connect_timeout=20,
                        pool_timeout=20
                    )
                except (BadRequest, TelegramError):
                    image_file.seek(0)
                    await message.reply_document(
                        document=image_file,
                        filename=filename,
                        read_timeout=60,
                        write_timeout=90,
                        connect_timeout=20,
                        pool_timeout=20
                    )
            else:
                await message.reply_document(
                    document=image_file,
                    filename=filename,
                    read_timeout=60,
                    write_timeout=90,
                    connect_timeout=20,
                    pool_timeout=20
                )
        except Exception as exc:
            # Only send a brief error when a supplied link cannot be processed.
            log.warning("Could not process image link %s: %s", url, exc)
            try:
                await message.reply_text("Image download nahi ho paya. Direct image link bhejein.")
            except Exception:
                pass


def run_web():
    # Bind to Render's assigned port; Flask runs separately from Telegram polling.
    web.run(host="0.0.0.0", port=PORT, use_reloader=False, threaded=True)


def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN environment variable is missing. Add it in Render Environment.")

    threading.Thread(target=run_web, daemon=True, name="health-web-server").start()

    request = HTTPXRequest(
        connect_timeout=30,
        read_timeout=60,
        write_timeout=90,
        media_write_timeout=120,
        connection_pool_size=8
    )
    app = Application.builder().token(BOT_TOKEN).request(request).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    log.info("Starting image downloader bot polling.")
    app.run_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
