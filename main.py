import os
import re
import json
import pytz
import logging
import asyncio
import threading
import requests
from datetime import datetime
from instaloader import Instaloader, Post
from dotenv import load_dotenv
from telegram import Update, ChatAction, InputFile
from telegram.constants import ParseMode
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters
)

# Load environment variables
load_dotenv()

# Logger setup
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# Config
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
USERNAME = os.getenv("INSTAGRAM_USERNAME")
PASSWORD = os.getenv("INSTAGRAM_PASSWORD")
TASHKENT_TZ = pytz.timezone("Asia/Tashkent")
USERS_LOG_FILE = "users.log"
ADMIN_FILE = "admin.json"
SESSION_FILE = f"{os.getcwd()}/session-{USERNAME}"

# Instaloader session
loader = Instaloader()
session_lock = threading.Lock()

def load_or_create_session():
    with session_lock:
        if os.path.exists(SESSION_FILE):
            loader.load_session_from_file(USERNAME, filename=SESSION_FILE)
        else:
            loader.login(USERNAME, PASSWORD)
            loader.save_session_to_file(SESSION_FILE)

load_or_create_session()

def get_admin():
    if os.path.exists(ADMIN_FILE):
        with open(ADMIN_FILE, "r") as file:
            return json.load(file).get("admin_id")
    return None

def set_admin(user_id):
    if not os.path.exists(ADMIN_FILE):
        with open(ADMIN_FILE, "w") as file:
            json.dump({"admin_id": user_id}, file)

def log_user_data(user):
    tashkent_time = datetime.now().astimezone(TASHKENT_TZ)
    user_data = {
        "user_id": user.id,
        "username": user.username,
        "first_name": user.first_name,
        "timestamp": tashkent_time.strftime("%Y-%m-%d %H:%M:%S"),
    }

    try:
        users = []
        if os.path.exists(USERS_LOG_FILE):
            with open(USERS_LOG_FILE, "r") as file:
                users = json.load(file)

        for existing_user in users:
            if existing_user["user_id"] == user_data["user_id"]:
                existing_user["timestamp"] = user_data["timestamp"]
                break
        else:
            users.append(user_data)

        with open(USERS_LOG_FILE, "w") as file:
            json.dump(users, file, indent=4)

    except Exception as e:
        logger.error(f"Error logging user data: {e}")

def extract_shortcode(instagram_post):
    match = re.search(r"instagram\\.com/(?:p|reel|tv)/([^/?#&]+)", instagram_post)
    return match.group(1) if match else None

def is_valid_instagram_url(url):
    return bool(re.match(r"https?://(www\\.)?instagram\\.com/(p|reel|tv)/", url))

def fetch_instagram_data(instagram_post):
    shortcode = extract_shortcode(instagram_post)
    if not shortcode:
        return None

    try:
        post = Post.from_shortcode(loader.context, shortcode)
        return post.video_url if post.is_video else post.url
    except Exception as e:
        logger.error(f"Error fetching Instagram data: {e}")
        return None

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    log_user_data(user)

    if get_admin() is None:
        set_admin(user.id)
        await update.message.reply_text("👑 You have been set as the admin!")

    await update.message.reply_text(
        "👋 Welcome to the Instagram Saver Bot!\n\n"
        "📩 Send me any **public** Instagram link (post, reel, or IGTV), and I'll fetch the media for you.\n"
        "⚠️ Make sure the post is **public** and not private.\n\n"
        "Happy downloading! 🎉"
    )

async def list_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user.id != get_admin():
        await update.message.reply_text("❌ You don't have permission to use this command.")
        return

    try:
        if not os.path.exists(USERS_LOG_FILE):
            await update.message.reply_text("No users found.")
            return

        with open(USERS_LOG_FILE, "r") as file:
            users = json.load(file)

        today = datetime.now(TASHKENT_TZ).date()
        today_users = sum(1 for u in users if datetime.strptime(u['timestamp'], "%Y-%m-%d %H:%M:%S").date() == today)

        response = f"📊 Total users: {len(users)}\n🌍 Used today: {today_users}\n\n"
        for u in users:
            response += (
                f"👤 User ID: {u['user_id']}\n"
                f"   Username: @{u['username'] or 'N/A'}\n"
                f"   First Name: {u['first_name']}\n"
                f"   Last Active: {u['timestamp']}\n\n"
            )
        await update.message.reply_text(response)
    except Exception as e:
        logger.error(f"Error reading user log: {e}")
        await update.message.reply_text("⚠️ An error occurred while retrieving user data.")

async def download(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    log_user_data(user)

    instagram_post = update.message.text.strip()
    if not is_valid_instagram_url(instagram_post):
        await update.message.reply_text("❌ Invalid Instagram URL.")
        return

    await update.message.chat.send_action(action=ChatAction.TYPING)
    progress_message = await update.message.reply_text("⏳ Fetching media...")

    media_url = fetch_instagram_data(instagram_post)
    if not media_url:
        await progress_message.edit_text("❌ Failed to fetch media. Make sure it is public.")
        return

    file_name = f"temp_{update.message.chat_id}.mp4" if "video" in media_url else f"temp_{update.message.chat_id}.jpg"

    try:
        response = requests.get(media_url, stream=True)
        with open(file_name, "wb") as f:
            for chunk in response.iter_content(1024):
                f.write(chunk)

        with open(file_name, "rb") as file:
            if file_name.endswith(".mp4"):
                await update.message.reply_video(video=InputFile(file), caption="👾 Powered by @Instasave_downloader_bot")
            else:
                await update.message.reply_photo(photo=InputFile(file), caption="👾 Powered by @Instasave_downloader_bot")

        await progress_message.delete()
    except Exception as e:
        logger.error(f"Send media error: {e}")
        await progress_message.edit_text("❌ Failed to send media.")
    finally:
        if os.path.exists(file_name):
            os.remove(file_name)

async def main():
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("users", list_users))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, download))

    logger.info("Bot started and polling...")
    await app.run_polling()

if __name__ == "__main__":
    asyncio.run(main())
