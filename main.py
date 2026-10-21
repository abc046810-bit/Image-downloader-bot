import asyncio
import ipaddress
import logging
import os
import re
import socket
import threading
from io import BytesIO
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

# FIX 1: Real browser User-Agent. Many CDNs (e.g. pview.myd-cdn.com) return
# 403 for non-browser User-Agents like "compatible; ImageDownloaderBot".
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
BROWSER_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "image/webp,image/apng,image/*,text/html;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Connection": "keep-alive",
}

# FIX 2: Hotlink-protected CDNs check the Referer header. We try several
# strategies one by one until one works:
#   "page"   -> page we came from (or the image site's own origin)
#   "google" -> looks like a visitor coming from Google
#   None     -> no Referer at all (some sites block unknown referers)
REFERER_MODES = ("page", "google", None)
RETRY_STATUS = {400, 401, 403, 406, 429}

IMAGE_TYPES = {
    "image/jpeg", "image/png", "image/webp", "image/gif",
    "image/bmp", "image/tiff", "image/avif"
}
EXT_BY_TYPE = {
    "image/jpeg": ".jpg", "image/png": ".png",
    "image/webp": ".webp", "image/gif": ".gif",
    "image/bmp": ".bmp", "image/tiff": ".tiff",
    "image/avif": ".avif",
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


def _sniff_image_type(head: bytes) -> str | None:
    """Detect the real image type from the first bytes (magic numbers).

    FIX 3: Some CDNs send images as application/octet-stream or with a wrong
    Content-Type. Checking the bytes makes those work too.
    """
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    if head.startswith(b"BM"):
        return "image/bmp"
    if head.startswith((b"II*\x00", b"MM\x00*")):
        return "image/tiff"
    if head[4:12] in (b"ftypavif", b"ftypavis"):
        return "image/avif"
    return None


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


def _read_limited(response, limit: int, chunk_size: int = 64 * 1024) -> bytes:
    chunks = []
    total = 0
    for chunk in response.iter_content(chunk_size=chunk_size):
        if not chunk:
            continue
        total += len(chunk)
        if total > limit:
            raise ValueError("Image is too large (maximum 18 MB).")
        chunks.append(chunk)
    return b"".join(chunks)


def _make_result(data: bytes, content_type: str, final_url: str) -> tuple[bytes, str, str]:
    if not data:
        raise ValueError("The image link returned an empty response.")

    # Trust the real bytes over the server's Content-Type header.
    sniffed = _sniff_image_type(data[:16])
    if sniffed:
        content_type = sniffed
    if content_type == "image/jpg":
        content_type = "image/jpeg"

    path_name = os.path.basename(urlparse(final_url).path) or "image"
    if "." not in path_name:
        path_name += EXT_BY_TYPE.get(content_type, ".img")
    return data, content_type, path_name[:100]


def _download_image_once(url: str, referer_mode) -> tuple[bytes, str, str]:
    """One full attempt using a single Referer strategy."""
    current_url = url
    previous_page = None
    session = requests.Session()
    session.headers.update(BROWSER_HEADERS)

    for _ in range(4):
        if not _public_http_url(current_url):
            raise ValueError("This link is not a valid public HTTP/HTTPS URL.")

        headers = {}
        if referer_mode == "page":
            if previous_page:
                headers["Referer"] = previous_page
            else:
                p = urlparse(current_url)
                headers["Referer"] = f"{p.scheme}://{p.netloc}/"
        elif referer_mode == "google":
            headers["Referer"] = "https://www.google.com/"

        with session.get(
            current_url, headers=headers, timeout=TIMEOUT,
            stream=True, allow_redirects=True
        ) as response:
            response.raise_for_status()
            final_url = response.url
            if not _public_http_url(final_url):
                raise ValueError("The link redirected to an unsupported address.")

            content_type = response.headers.get("Content-Type", "").split(";")[0].strip().lower()

            # Normal image response.
            if content_type in IMAGE_TYPES or content_type.startswith("image/"):
                length = response.headers.get("Content-Length")
                if length and length.isdigit() and int(length) > MAX_BYTES:
                    raise ValueError("Image is too large (maximum 18 MB).")
                data = _read_limited(response, MAX_BYTES)
                return _make_result(data, content_type, final_url)

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
                raw = b"".join(html_chunks)

                # Some servers label an image as text/html or omit the type.
                if _sniff_image_type(raw[:16]):
                    return _make_result(raw, content_type, final_url)

                html = raw.decode(response.encoding or "utf-8", errors="replace")
                next_url = _extract_image_url(final_url, html)
                if not next_url or next_url == current_url:
                    raise ValueError("No image was found on this page. Please send a direct image link.")
                previous_page = final_url
                current_url = next_url
                continue

            # FIX 3: application/octet-stream, binary/octet-stream etc.
            # Download it and check the real bytes.
            if content_type in (
                "application/octet-stream", "binary/octet-stream",
                "application/binary", "application/x-binary", "application/download",
            ):
                data = _read_limited(response, MAX_BYTES)
                if _sniff_image_type(data[:16]):
                    return _make_result(data, content_type, final_url)

            raise ValueError("This link does not appear to contain an image.")

    raise ValueError("Could not find the image after following the page link.")


def _download_image(url: str) -> tuple[bytes, str, str]:
    """Return (bytes, content-type, filename); supports direct links and image-host pages.

    Tries multiple Referer strategies when the server blocks us (403 etc.).
    """
    last_exc = None
    for mode in REFERER_MODES:
        try:
            return _download_image_once(url, mode)
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else None
            log.info("Attempt failed (referer=%s) for %s: HTTP %s", mode, url, status)
            last_exc = exc
            if status not in RETRY_STATUS:
                raise
    raise last_exc


async def _send_image(message, data: bytes, content_type: str, filename: str) -> None:
    image_file = BytesIO(data)
    image_file.name = filename
    timeouts = dict(
        read_timeout=60, write_timeout=90, connect_timeout=20, pool_timeout=20
    )

    # Telegram photos: common formats only. Others go as documents (original bytes).
    if content_type in ("image/jpeg", "image/png", "image/webp"):
        try:
            await message.reply_photo(photo=image_file, **timeouts)
            return
        except (BadRequest, TelegramError):
            image_file.seek(0)

    await message.reply_document(document=image_file, filename=filename, **timeouts)


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
            # FIX 4: requests is blocking. Run it in a thread so the bot (and
            # the Render health check) is not frozen while an image downloads.
            data, content_type, filename = await asyncio.to_thread(_download_image, url)
            await _send_image(message, data, content_type, filename)
        except requests.RequestException as exc:
            log.warning("Local download failed for %s: %s", url, exc)
            # FIX 5: Last resort. Let Telegram's own servers fetch the URL.
            # Their IPs are different from Render's, so this can pass where
            # Render's IP is blocked by the CDN.
            try:
                await message.reply_photo(
                    photo=url,
                    read_timeout=60, write_timeout=90,
                    connect_timeout=20, pool_timeout=20
                )
            except Exception as exc2:
                log.warning("Telegram-side fetch also failed for %s: %s", url, exc2)
                try:
                    await message.reply_text("Image download nahi ho paya. Direct image link bhejein.")
                except Exception:
                    pass
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
