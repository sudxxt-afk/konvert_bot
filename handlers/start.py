import asyncio
import html

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, FSInputFile, Message

from config import MAX_FILE_MB, TMP_DIR
from db import db
from keyboards import _btn, categories_menu, category_back
from services.converter import make_qr_png

router = Router(name="start")

BRAND = "KudexConvert"

WELCOME = (
    "👋 <b>Привет, {name}!</b>\n\n"
    f"Я — <b>{BRAND}</b>, конвертер всего на свете. Работаю прямо здесь.\n\n"
    "<blockquote expandable>🚀 <b>Как это работает</b>\n"
    "1️⃣ Пришли файл (или выбери категорию ниже)\n"
    "2️⃣ Покажу кнопками все доступные форматы\n"
    "3️⃣ Нажми — и забирай результат</blockquote>\n\n"
    "🔗 Мгновенный QR: <code>/qr текст</code>\n\n"
    "🛠 Владелец: @inhgalator"
)

CATEGORY_CARDS = {
    "video": (
        "🎬 <b>Видео и кружочки</b>\n\n"
        "<b>Форматы:</b>\n"
        "🎤 <b>Войс</b> — звук в виде голосового\n"
        "🎵 <b>MP3</b> / 🎼 <b>WAV</b> / 🎧 <b>M4A</b> / 💿 <b>FLAC</b> — аудиодорожка\n"
        "🎞 <b>GIF</b> — анимация до 15 секунд (480px)\n"
        "📦 <b>MP4</b> — нормализация для любых устройств\n"
        "🌐 <b>WebM</b> — сильное сжатие\n"
        "🔇 <b>Убрать звук</b> — тихое видео\n"
        "🩹 <b>Видео-стикер</b> — WebM 512px, до 3 сек\n\n"
        "<blockquote expandable>💡 <b>Советы</b>\n"
        "• Кружочки конвертируются так же, как видео\n"
        "• MP4 пересобирается в H.264 + AAC\n"
        "• Лимит Telegram — 19 МБ на скачивание</blockquote>\n\n"
        "📎 <i>Пришли видео следующим сообщением 👇</i>"
    ),
    "audio": (
        "🎵 <b>Аудио и голосовые</b>\n\n"
        "<b>Форматы:</b>\n"
        "🎵 <b>MP3</b> — универсальный (~160 kbps VBR)\n"
        "🎼 <b>WAV</b> — без потерь, для монтажа\n"
        "🎧 <b>M4A</b> — компактный, для Apple\n"
        "💿 <b>FLAC</b> — lossless архив\n"
        "🌀 <b>OGG</b> — лёгкий стриминговый\n"
        "🎤 <b>Войс</b> — трек внутри кружочка-войса\n"
        "📱 <b>Рингтон M4R</b> — первые 30 сек для iPhone\n\n"
        "<blockquote expandable>💡 <b>Советы</b>\n"
        "• Голосовые от друзей конвертируются тоже\n"
        "• M4R кидай в iPhone через iCloud/AirDrop</blockquote>\n\n"
        "📎 <i>Пришли аудио или голосовое 👇</i>"
    ),
    "photo": (
        "🖼 <b>Фото</b>\n\n"
        "<b>Форматы:</b>\n"
        "🖼 <b>JPEG</b> · 🎨 <b>PNG</b> · 🌐 <b>WebP</b> · 🗂 <b>TIFF</b> · 🖌 <b>BMP</b>\n"
        "🎯 <b>ICO</b> — иконка/фавиконка сайта\n"
        "🩹 <b>Под стикер</b> — 512px WebP для стикерпаков\n"
        "📦 <b>Сжать</b> — до −70% веса\n\n"
        "<b>Эффекты:</b>\n"
        "⚫️ Ч/Б · 🌗 Инверсия · ↻ Поворот 90°\n\n"
        "<blockquote expandable>💡 <b>Советы</b>\n"
        "• 🍏 HEIC с iPhone — открою и сконвертирую\n"
        "• После сжатия покажу, сколько сэкономил\n"
        "• 🔍 Найду QR-код на картинке\n"
        "• ℹ️ Покажу разрешение и метаданные</blockquote>\n\n"
        "📎 <i>Пришли фото следующим сообщением 👇</i>"
    ),
    "doc": (
        "📄 <b>Документы и данные</b>\n\n"
        "<b>Конвертации:</b>\n"
        "📝 <b>DOCX → TXT</b> — включая таблицы\n"
        "📊 <b>XLSX → CSV</b> и <b>CSV → XLSX</b>\n"
        "💬 <b>SRT/VTT → TXT</b> — субтитры без таймкодов\n"
        "📃 <b>TXT → PDF</b> — аккуратный документ с кириллицей\n"
        "✂️ <b>PDF по страницам</b> — ZIP с одностраничниками\n"
        "📦 <b>Сжать PDF</b> — легче в разы\n\n"
        "<blockquote expandable>💡 <b>Советы</b>\n"
        "• Сканы PDF без текстового слоя не распознаю\n"
        "• У любого файла есть ℹ️ Инфо и #️⃣ Хеши MD5/SHA256\n"
        "• Лимит Telegram — 19 МБ</blockquote>\n\n"
        "📎 <i>Пришли документ следующим сообщением 👇</i>"
    ),
    "qr": (
        "🔗 <b>QR-коды</b>\n\n"
        "<b>Создать QR:</b>\n"
        "1️⃣ Отправь команду <code>/qr твой текст или ссылка</code>\n"
        "2️⃣ Или пришли файл <code>.txt</code> — нажми «🔗 Сделать QR»\n\n"
        "<b>Распознать QR:</b>\n"
        "🔍 Пришли фото с QR-кодом — нажми «Найти QR»,\n"
        "и я вытащу из него текст\n\n"
        "<blockquote expandable>💡 <b>Советы</b>\n"
        "• До 1500 символов в одном коде\n"
        "• Ссылки, Wi-Fi, контакты — что угодно</blockquote>"
    ),
    "tools": (
        "🔧 <b>Инструменты</b>\n\n"
        "Доступны для <b>любого</b> присланного файла:\n\n"
        "ℹ️ <b>Инфо о файле</b> — тип, размер, длительность,\n"
        "разрешение, кодеки, битрейт\n\n"
        "#️⃣ <b>Хеши MD5 / SHA256</b> — проверь подлинность файла,\n"
        "сравни с источником\n\n"
        "<blockquote expandable>💡 <b>Зачем хеши?</b>\n"
        "Скачал ISO или архив? Сравни SHA256 с официальным сайтом —\n"
        "совпал значит файл не подменён.</blockquote>\n\n"
        "📎 <i>Пришли любой файл 👇</i>"
    ),
}


def _render_welcome(first_name: str | None) -> str:
    name = html.escape(first_name or "друг")
    return WELCOME.replace("{name}", name)


async def _render_stats(user_id: int) -> str:
    stats = await db.stats(user_id)
    total_all = await db.global_total()
    lines = [
        "📊 <b>Твоя статистика</b>\n",
        f"🔢 Твоих конвертаций: <b>{stats['total']}</b>",
        f"🌍 Всего у бота: <b>{total_all}</b>",
    ]
    if stats.get("created"):
        lines.append(f"📆 С нами с: <b>{stats['created']}</b>")
    lines.append(f"\n⚖️ Лимит Telegram: файлы до {MAX_FILE_MB} МБ")
    return "\n".join(lines)


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    await db.ensure_user(message.from_user.id, message.from_user.username)
    await message.answer(
        _render_welcome(message.from_user.first_name),
        reply_markup=categories_menu(),
    )


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(
        "✨ <b>Возможности бота</b>\n\nВыбери категорию:",
        reply_markup=categories_menu(),
    )


@router.message(Command("status"))
async def cmd_status(message: Message) -> None:
    user_id = message.from_user.id
    await db.ensure_user(user_id, message.from_user.username)
    await message.answer(await _render_stats(user_id), reply_markup=category_back())


@router.message(Command("qr"))
async def cmd_qr(message: Message, bot: Bot, command: CommandObject) -> None:
    text = (command.args or "").strip()
    if not text:
        await message.answer(
            "🔗 <b>Генератор QR-кодов</b>\n\n"
            "Использование: <code>/qr твой текст или ссылка</code>\n"
            "Или пришли файл .txt — сделаю QR из содержимого."
        )
        return
    if len(text) > 1500:
        await message.answer("😔 Слишком длинный текст — максимум 1500 символов.")
        return
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    path = await asyncio.to_thread(make_qr_png, text)
    try:
        await message.answer_document(FSInputFile(path), caption="🔗 <b>QR готов!</b>")
    finally:
        path.unlink(missing_ok=True)


@router.callback_query(F.data == "cats")
async def cb_categories(callback: CallbackQuery) -> None:
    await callback.answer()
    await callback.message.edit_text(
        "🗂 <b>Категории файлов</b>\n\nВыбери, с чем работаешь:",
        reply_markup=categories_menu(),
    )


@router.callback_query(F.data.startswith("cat:"))
async def cb_category(callback: CallbackQuery) -> None:
    code = callback.data.split(":", 1)[1]
    card = CATEGORY_CARDS.get(code)
    if card is None:
        await callback.answer("Категория не найдена", show_alert=True)
        return
    await callback.answer()
    await callback.message.edit_text(card, reply_markup=category_back())


@router.callback_query(F.data == "my_stats")
async def cb_my_stats(callback: CallbackQuery) -> None:
    await callback.answer()
    text = await _render_stats(callback.from_user.id)
    await callback.message.edit_text(text, reply_markup=category_back())


HOWTO = (
    "❓ <b>Как пользоваться</b>\n\n"
    "<blockquote expandable>🚀 <b>Три шага</b>\n"
    "1️⃣ Пришли файл — видео, музыку, фото, PDF, DOCX, XLSX\n"
    "2️⃣ Бот покажет кнопки с форматами\n"
    "3️⃣ Нажми кнопку — забери результат</blockquote>\n\n"
    "<blockquote expandable>🔗 <b>QR-коды</b>\n"
    "Создать: <code>/qr текст</code> или пришли .txt\n"
    "Распознать: пришли фото с QR → «Найти QR»</blockquote>\n\n"
    "<blockquote expandable>🧰 <b>Ещё умею</b>\n"
    "• ⚡️ Инлайн: набери <code>@kudexconvert_bot текст</code> в любом чате — QR\n"
    "• 📸 Альбом фото — обработаю пачкой\n"
    "• ℹ️ Инфо о любом файле (кодеки, размер)\n"
    "• #️⃣ Хеши MD5/SHA256 для проверки подлинности\n"
    "• 📦 Сжатие фото и PDF до −70% веса\n"
    "• 🩹 Стикеры из фото и видео 512px\n"
    "• 📱 Рингтон M4R для iPhone (30 сек)</blockquote>\n\n"
    f"⚖️ Лимит Telegram для ботов — {MAX_FILE_MB} МБ на файл."
)


def _howto_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[_btn("⬅️ В начало", "back_start")]]
    )


@router.callback_query(F.data == "howto")
async def cb_howto(callback: CallbackQuery) -> None:
    await callback.answer()
    await callback.message.edit_text(HOWTO, reply_markup=_howto_kb())


@router.callback_query(F.data == "back_start")
async def cb_back_start(callback: CallbackQuery) -> None:
    await callback.answer()
    await callback.message.edit_text(
        _render_welcome(callback.from_user.first_name),
        reply_markup=categories_menu(),
    )
