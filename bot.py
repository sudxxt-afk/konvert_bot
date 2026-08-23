import asyncio
import logging
import shutil
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from config import BOT_TOKEN
from db import db
from handlers import convert, start


async def main() -> None:
    if not BOT_TOKEN:
        sys.exit(
            "❌ BOT_TOKEN не задан.\n"
            "Скопируйте .env.example в .env и вставьте токен от @BotFather."
        )
    if shutil.which("ffmpeg") is None:
        print(
            "⚠️  ffmpeg не найден: видео/аудио конвертация работать не будет.\n"
            "   Windows: winget install Gyan.FFmpeg\n"
            "   macOS:   brew install ffmpeg\n"
            "   Linux:   sudo apt install ffmpeg"
        )

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    logging.getLogger("aiogram.event").setLevel(logging.WARNING)

    await db.init()

    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.include_routers(start.router, convert.router)

    try:
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot)
    finally:
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
