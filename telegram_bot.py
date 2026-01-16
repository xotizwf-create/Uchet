import asyncio
import os

import requests
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes


def _load_env_file(path: str = ".env") -> None:
    if not os.path.exists(path):
        return
    try:
        with open(path, "r", encoding="utf-8") as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip().strip("'").strip('"')
                if key and key not in os.environ:
                    os.environ[key] = value
    except OSError as exc:
        print(f"Failed to load {path}: {exc}")


_load_env_file()

BASE_URL = os.getenv("BASE_URL", "http://127.0.0.1:5000").rstrip("/")
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    if not context.args:
        await update.message.reply_text("Send /start <token> from the login page to link your account.")
        return
    token = context.args[0]
    payload = {
        "token": token,
        "telegram_user_id": str(update.effective_user.id),
        "chat_id": str(update.effective_chat.id),
    }

    async def _post_link():
        return requests.post(f"{BASE_URL}/telegram/link", json=payload, timeout=10)

    try:
        response = await asyncio.to_thread(_post_link)
    except Exception:  # noqa: BLE001
        await update.message.reply_text("Linking failed. Check BASE_URL and server availability.")
        return
    if response.ok:
        await update.message.reply_text("Linked. Check the OTP code sent to this chat.")
        return
    error_detail = ""
    try:
        data = response.json()
        if isinstance(data, dict) and data.get("error"):
            error_detail = f" ({data['error']})"
    except Exception:  # noqa: BLE001
        error_detail = ""
    await update.message.reply_text(f"Linking failed{error_detail}. Try again.")


def main() -> None:
    if not BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set")
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.run_polling()


if __name__ == "__main__":
    main()
