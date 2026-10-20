import os
import logging
import requests
import asyncio
from flask import Flask, request
from telegram import Update, Bot
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# ---------------- CONFIG ----------------
TOKEN = os.environ.get("TELEGRAM_TOKEN")
PORT = int(os.environ.get("PORT", 10000))
WEBHOOK_URL = os.environ.get("WEBHOOK_URL")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
bot = Bot(token=TOKEN)
application = Application.builder().token(TOKEN).build()

# ---------------- HANDLERS ----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Send me any image URL and I will download & send it back.\n\n"
        "Example:\nhttps://image.tmdb.org/t/p/w342/1nej9lnFyZTlqlaP6XHUEPtVYyX.jpg"
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Just paste any direct image link (jpg, png, webp, etc.)\n"
        "I will download and send the image."
    )

async def handle_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    text = update.message.text.strip()

    # Ignore commands
    if text.startswith("/"):
        return

    if not text.startswith(("http://", "https://")):
        await update.message.reply_text("Please send a valid image URL starting with http or https")
        return

    await update.message.reply_chat_action("upload_photo")

    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        response = requests.get(text, headers=headers, timeout=20)
        response.raise_for_status()

        content_type = response.headers.get("Content-Type", "").lower()
        is_image = any(x in content_type for x in ["image/", "jpeg", "png", "webp", "gif"])
        has_image_ext = any(text.lower().endswith(ext) for ext in [".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"])

        if not (is_image or has_image_ext):
            await update.message.reply_text("This doesn't look like a direct image link.")
            return

        await update.message.reply_photo(
            photo=response.content,
            caption="Here's your image ✅"
        )

    except requests.exceptions.Timeout:
        await update.message.reply_text("Download timed out. Try again.")
    except requests.exceptions.RequestException as e:
        logger.error(f"Download error: {e}")
        await update.message.reply_text("Failed to download the image. Link may be private or invalid.")
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        await update.message.reply_text("Something went wrong. Please try another link.")

# Register handlers
application.add_handler(CommandHandler("start", start))
application.add_handler(CommandHandler("help", help_command))
application.add_handler(MessageHandler(filters.TEXT, handle_url))   # ← yahan \~ nahi hai

# ---------------- FLASK ROUTES ----------------
@app.route("/")
def home():
    return "Image Downloader Bot is running! ✅"

@app.route("/webhook", methods=["POST"])
def webhook():
    update = Update.de_json(request.get_json(force=True), bot)
    asyncio.get_event_loop().run_until_complete(application.process_update(update))
    return "ok"

# ---------------- STARTUP ----------------
if __name__ == "__main__":
    if WEBHOOK_URL:
        try:
            bot.set_webhook(url=f"{WEBHOOK_URL.rstrip('/')}/webhook")
            print(f"Webhook set to: {WEBHOOK_URL.rstrip('/')}/webhook")
        except Exception as e:
            print(f"Webhook set failed: {e}")
    
    app.run(host="0.0.0.0", port=PORT)
