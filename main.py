import os
import logging
import requests
import asyncio
from flask import Flask, request
from telegram import Update, Bot
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# ---------------- CONFIG ----------------
TOKEN = os.environ.get("BOT_TOKEN", "").strip()
PORT = int(os.environ.get("PORT", 10000))
WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "").strip()

print("=" * 50)
print(f"TOKEN present: {bool(TOKEN)}")
print(f"TOKEN length: {len(TOKEN) if TOKEN else 0}")
print(f"WEBHOOK_URL: {WEBHOOK_URL}")
print("=" * 50)

if not TOKEN:
    raise ValueError("BOT_TOKEN is missing!")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
application = Application.builder().token(TOKEN).build()

# ---------------- HANDLERS ----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "✅ Bot is working!\n\n"
        "Ab koi bhi image link bhejo, main download karke bhej dunga."
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Koi bhi direct image URL bhejo (jpg / png / webp)")

async def handle_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    text = update.message.text.strip()

    if text.startswith("/"):
        return

    if not text.startswith(("http://", "https://")):
        await update.message.reply_text("Valid image URL bhejo (http ya https se start hona chahiye)")
        return

    await update.message.reply_chat_action("upload_photo")

    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
        r = requests.get(text, headers=headers, timeout=20)
        r.raise_for_status()

        content_type = r.headers.get("Content-Type", "").lower()
        is_image = any(x in content_type for x in ["image/", "jpeg", "png", "webp", "gif"])
        has_ext = any(text.lower().endswith(ext) for ext in [".jpg", ".jpeg", ".png", ".webp", ".gif"])

        if not (is_image or has_ext):
            await update.message.reply_text("Yeh direct image link nahi lag raha.")
            return

        await update.message.reply_photo(photo=r.content, caption="Here's your image ✅")

    except Exception as e:
        logger.error(f"Download error: {e}")
        await update.message.reply_text("Download fail ho gaya. Link check karo.")

# Register handlers
application.add_handler(CommandHandler("start", start))
application.add_handler(CommandHandler("help", help_command))
application.add_handler(MessageHandler(filters.TEXT, handle_url))

# ---------------- FLASK ROUTES ----------------
@app.route("/")
def home():
    return "Image Downloader Bot is LIVE ✅"

@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        data = request.get_json(force=True)
        update = Update.de_json(data, application.bot)

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(application.process_update(update))
        loop.close()

        return "ok"
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return "error", 500

# ---------------- SET WEBHOOK PROPERLY ----------------
async def setup_webhook():
    if not WEBHOOK_URL:
        print("❌ WEBHOOK_URL missing")
        return

    url = f"{WEBHOOK_URL.rstrip('/')}/webhook"
    
    try:
        # First delete old webhook
        await application.bot.delete_webhook(drop_pending_updates=True)
        
        # Set new webhook
        await application.bot.set_webhook(url=url)
        print(f"✅ Webhook successfully set to: {url}")
    except Exception as e:
        print(f"❌ Failed to set webhook: {e}")

# Run the async setup
asyncio.run(setup_webhook())

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT)
