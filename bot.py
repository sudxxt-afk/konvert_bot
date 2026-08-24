import asyncio
import logging
import shutil
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand

from config import BOT_TOKEN
from db import db
from handlers import admin, convert, inline, start


async def _setup_bot_info(bot: Bot) -> None:
    await bot.set_my_commands(
        [
            BotCommand(command="start", description="🚀 Запустить бота"),
            BotCommand(command="status", description="📊 Моя статистика"),
            BotCommand(command="qr", description="🔗 QR-код из текста"),
            BotCommand(command="help", description="✨ Возможности"),
        ]
    )
    await bot.set_my_description(
        "🔄 KudexConvert — универсальный конвертер файлов в Telegram\n\n"
        "🎬 Видео → войс · MP3 · GIF · WebM · видео-стикер\n"
        "🎵 Аудио → MP3 · WAV · M4A · FLAC · рингтон M4R\n"
        "🖼 Фото → JPEG · PNG · WebP · стикер 512px · сжатие до −70%\n"
        "📄 Документы → PDF · TXT · DOCX · XLSX · CSV · субтитры\n\n"
        "🔗 QR-коды из текста + распознавание с фото\n"
        "ℹ️ Инфо о файле · хеши MD5/SHA256\n"
        "📸 Альбомы — обработка пачкой\n"
        "✂️ PDF по страницам или сжатие в разы\n\n"
        "⚡️ Бесплатно · Без рекламы · Без лимитов\n"
        "👤 Владелец: @inhgalator"
    )
    await bot.set_my_short_description(
        "🔄 Конвертер всего: видео · аудио · фото · PDF · QR.\n"
        "Бесплатно, без лимитов ⚡\n"
        "Владелец: @inhgalator"
    )


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
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_routers(admin.router, start.router, inline.router, convert.router)

    try:
        await bot.delete_webhook(drop_pending_updates=True)
        await _setup_bot_info(bot)
        await dp.start_polling(bot)
    finally:
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
