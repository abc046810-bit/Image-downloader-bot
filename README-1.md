# Image Downloader Telegram Bot

Simple Telegram bot: send one or more HTTP/HTTPS image links and it replies with the image. It supports direct image URLs and many image-hosting pages that expose an `og:image`/`twitter:image` preview.

## Files
- `main.py` — bot + Render health endpoint
- `requirements.txt` — pinned dependencies
- `runtime.txt` — Python 3.12 runtime
- `render.yaml` — Render Web Service settings

## Deploy on Render Free Web Service
1. Upload these files to the root of your GitHub repository.
2. In Render, create a **Web Service** connected to that repository.
3. Build command: `pip install -r requirements.txt`
4. Start command: `python main.py`
5. Add environment variable `BOT_TOKEN` with the token from BotFather. Do not put the token in GitHub.
6. Set `PYTHON_VERSION` to `3.12.11` if Render does not pick it up from `runtime.txt`.
7. Deploy. The `/health` route should show `{"status":"ok"}`.

## Important
- If the same bot token is running in another bot instance, stop the other instance; Telegram polling allows only one active poller per token.
- Common JPEG/PNG/WEBP images are sent as Telegram photos. Other image formats are sent as documents to preserve their original bytes, because Telegram's photo endpoint does not support every image format.
- A link that only shows a page but does not expose an image preview may not be resolvable. The bot does not bypass logins, paywalls, or anti-bot protections.
