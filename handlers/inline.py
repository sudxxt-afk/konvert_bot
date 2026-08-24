import asyncio
import hashlib
import logging
from collections import OrderedDict

from aiogram import Bot, Router
from aiogram.types import (
    FSInputFile,
    InlineQuery,
    InlineQueryResultCachedPhoto,
    InlineQueryResultsButton,
)

from config import QR_UPLOAD_CHANNEL
from services.converter import make_qr_png

logger = logging.getLogger(__name__)
router = Router(name="inline")

_cache: OrderedDict[str, str] = OrderedDict()
_CACHE_MAX = 300


@router.inline_query()
async def inline_qr(query: InlineQuery, bot: Bot) -> None:
    text = (query.query or "").strip()
    if not text:
        await query.answer(
            results=[],
            button=InlineQueryResultsButton(text="Введи текст — сделаю QR 🔗"),
        )
        return

    key = hashlib.sha256(text.encode()).hexdigest()[:16]
    file_id = _cache.get(key)

    if not file_id:
        if not QR_UPLOAD_CHANNEL:
            await query.answer(
                results=[],
                switch_pm_text="Открыть бота для QR",
                switch_pm_parameter="from_inline",
                cache_time=10,
            )
            return
        png = await asyncio.to_thread(make_qr_png, text[:1000])
        try:
            msg = await bot.send_photo(chat_id=QR_UPLOAD_CHANNEL, photo=FSInputFile(png))
            file_id = msg.photo[-1].file_id
            _cache[key] = file_id
            _cache.move_to_end(key)
            while len(_cache) > _CACHE_MAX:
                _cache.popitem(last=False)
        except Exception:
            logger.exception("inline qr upload failed")
            await query.answer(results=[], cache_time=5)
            return
        finally:
            png.unlink(missing_ok=True)

    await query.answer(
        results=[
            InlineQueryResultCachedPhoto(
                id=key,
                photo_file_id=file_id,
                title="🔗 QR готов",
                description=text[:100],
                caption=f"🔗 {text[:200]}",
            )
        ],
        cache_time=300,
        is_personal=True,
    )
