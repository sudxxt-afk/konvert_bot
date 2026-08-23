# 🔄 Konvert Bot — универсальный конвертер файлов для Telegram

Бесплатный бот-конвертер без лимитов и рекламы. Просто отправь файл — бот предложит варианты конвертации.

## Возможности

| Исходник | Конвертация |
|---|---|
| 🎬 Видео | → голосовое (OGG), MP3, GIF (до 15 сек) |
| ⭕️ Кружочки | → MP3, WAV, M4A, голосовое |
| 🎧 Аудио / голосовые | → MP3, WAV, M4A, голосовое |
| 🖼 Фото | → JPEG, PNG, WebP + сжатие |
| 📄 PDF | → TXT |

## Запуск через Docker (рекомендуется)

Нужен только [Docker](https://docs.docker.com/get-docker/) — Python и ffmpeg уже внутри образа.

```bash
cp .env.example .env        # вставь токен в BOT_TOKEN
docker compose up -d --build
docker compose logs -f      # смотреть логи
```

Полезные команды:

```bash
docker compose down         # остановить
docker compose up -d --build  # пересобрать после изменений
docker compose ps           # статус + healthcheck
```

База данных хранится в Docker-томе `bot-data` — переживает перезапуски и пересборки. Временные файлы конвертации живут в tmpfs (в памяти) и не нагружают диск.

## Ручной запуск (без Docker)

**1. Установи [Python 3.10+](https://python.org) и ffmpeg:**

```bash
# Windows
winget install Gyan.FFmpeg

# macOS
brew install ffmpeg

# Linux (Debian/Ubuntu)
sudo apt install ffmpeg
```

**2. Получи токен у [@BotFather](https://t.me/BotFather)** (`/newbot`).

**3. Настрой проект:**

```bash
git clone https://github.com/sudxxt-afk/konvert_bot.git
cd konvert_bot
pip install -r requirements.txt
copy .env.example .env        # Windows
# или: cp .env.example .env   # Linux/macOS
```

**4. Впиши токен в `.env`** в поле `BOT_TOKEN=...`

**5. Запусти:**

```bash
python bot.py
```

## Структура проекта

```
bot.py                  # точка входа
config.py               # конфигурация (.env)
db.py                   # SQLite-статистика пользователей
keyboards.py            # inline-клавиатуры
Dockerfile              # образ (python 3.12-slim + ffmpeg)
compose.yml             # оркестрация: тома, tmpfs, healthcheck
handlers/
    start.py            # /start, /status, /help
    convert.py          # приём файлов и конвертация
services/
    converter.py        # ffmpeg / Pillow / pypdf
```

## Как это работает

1. Пользователь отправляет файл → бот определяет тип (видео/аудио/фото/PDF)
2. Показывает кнопки с доступными конвертациями
3. Скачивает файл во временную папку, конвертирует, отправляет результат и удаляет временные файлы

Лимит Telegram Bot API — **20 МБ** на скачивание файла, поэтому файлы больше 19 МБ отклоняются с понятным сообщением.

## Roadmap

- [ ] Монетизация через Telegram Stars (Premium без лимитов)
- [ ] Реферальная система
- [ ] Больше форматов (DOCX, видео → аудио дорожки, обрезка)

## Лицензия

MIT
