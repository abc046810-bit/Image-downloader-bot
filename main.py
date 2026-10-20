import os
import logging
import requests
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# ---------------- CONFIG ----------------
TOKEN = os.environ.get("BOT_TOKEN", "").strip()

if not TOKEN:
    raise ValueError("BOT_TOKEN environment variable is missing!")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

print("=" * 50)
print(f"TOKEN present: True")
print(f"TOKEN length: {len(TOKEN)}")
print("Starting bot in POLLING mode...")
print("=" * 50)

# ---------------- HANDLERS ----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "✅ Bot is working!\n\n"
        "Ab koi bhi image link bhejo, main download karke bhej dunga.\n\n"
        "Example:\nhttps://image.tmdb.org/t/p/w342/1nej9lnFyZTlqlaP6XHUEPtVYyX.jpg"
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

    # Send "uploading" action
    await update.message.reply_chat_action("upload_photo")

    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        response = requests.get(text, headers=headers, timeout=25)
        response.raise_for_status()

        content_type = response.headers.get("Content-Type", "").lower()
        is_image = any(x in content_type for x in ["image/", "jpeg", "png", "webp", "gif"])
        has_ext = any(text.lower().endswith(ext) for ext in [".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"])

        if not (is_image or has_ext):
            await update.message.reply_text("Yeh direct image link nahi lag raha.")
            return

        # Send the image
        await update.message.reply_photo(
            photo=response.content,
            caption="Here's your image ✅"
        )

    except requests.exceptions.Timeout:
        await update.message.reply_text("Download timeout ho gaya. Thodi der baad try karo.")
    except Exception as e:
        logger.error(f"Error: {e}")
        await update.message.reply_text("Download fail ho gaya. Link check karo ya dusra link try karo.")

# ---------------- MAIN ----------------
def main():
    application = Application.builder().token(TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(MessageHandler(filters.TEXT & \~filters.COMMAND, handle_url))

    print("Bot is starting polling...")
    application.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

if __name__ == "__main__":
    main()
