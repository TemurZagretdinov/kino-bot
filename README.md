# Telegram kino kod bot

Python 3.11+ va aiogram 3 asosida yozilgan Telegram bot. Foydalanuvchi botga kino kodini yuboradi, bot avval majburiy Telegram kanallarga obuna holatini tekshiradi. Agar foydalanuvchi barcha aktiv kanallarga obuna bo‘lgan bo‘lsa, bot bazadagi mos arxiv kanal postini `copy_message` orqali yuboradi.

Loyiha noqonuniy kontent tarqatish uchun emas, balki admin tomonidan kiritilgan link va ma’lumotlarni boshqarish uchun mo‘ljallangan. Har qanday linkni joylashdan oldin mualliflik huquqi va platforma qoidalariga rioya qiling.

## Texnologiyalar

- Python 3.11+
- aiogram 3
- SQLAlchemy async
- SQLite + aiosqlite
- PostgreSQL + asyncpg
- python-dotenv
- pydantic-settings

## O‘rnatish

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

Linux yoki macOS:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## .env sozlash

`.env.example` faylidan `.env` fayl yarating:

```bash
copy .env.example .env
```

Linux yoki macOS:

```bash
cp .env.example .env
```

`.env` ichini to‘ldiring:

```env
BOT_TOKEN=your_bot_token_here
ADMIN_IDS=123456789,987654321
DATABASE_URL=sqlite+aiosqlite:///bot.db
```

`ADMIN_IDS` ichiga admin Telegram IDlari vergul bilan yoziladi.

## Ishga tushirish

```bash
python bot.py
```

Birinchi ishga tushirishda tanlangan database ichida kerakli jadvallar avtomatik yaratiladi.

## Majburiy obuna

Admin panel orqali qo‘shilgan va `is_active=True` bo‘lgan kanallar majburiy obuna sifatida tekshiriladi.

Admin `📢 Kanal qo‘shish` bo‘limida kanal linkini yuboradi. Bot linkni analiz qilib public yoki private kanal ekanini aniqlaydi:

- Public kanal linklari: `https://t.me/channel_username`, `t.me/channel_username`, `@channel_username`. Bot username’ni avtomatik `@channel_username` ko‘rinishida ajratadi. Obuna tekshiruvda bot `getChatMember(username, user_id)` ishlatadi.
- Private kanal linklari: `https://t.me/+abcdef`, `https://t.me/joinchat/abcdef`. Bot invite linkni saqlaydi va admindan shu private kanaldan istalgan bitta postni botga forward qilishni so‘raydi. Forward qilingan postdagi `forward_origin` orqali kanal `chat_id` avtomatik olinadi. Obuna tekshiruvda bot `getChatMember(chat_id, user_id)` ishlatadi.

Muhim eslatmalar:

- Public kanal uchun bot o‘sha kanalda admin bo‘lishi kerak.
- Private kanal uchun ham bot o‘sha kanalda admin bo‘lishi kerak.
- Private kanal tekshiruvida invite link emas, forward qilingan postdan olingan `chat_id` ishlatiladi.
- `invite_link` faqat foydalanuvchini kanalga olib kirish tugmasi uchun ishlatiladi.
- Obuna deb faqat `member`, `administrator`, `creator` statuslari hisoblanadi.
- `left`, `kicked` va kanalni ko‘ra olmaydigan `restricted` holatlari obuna emas deb hisoblanadi.

Foydalanuvchi obuna bo‘lmagan bo‘lsa, bot kanalga o‘tish tugmalari va `✅ Tekshirish` tugmasini chiqaradi. Tekshirish bosilganda bot userning database’da saqlangan oxirgi `last_movie_code` qiymati orqali kino qidirishni davom ettiradi.

Agar bot kanal admini bo‘lmasa, `chat_id`/`username` noto‘g‘ri bo‘lsa yoki Telegram tekshiruvida xatolik qaytsa, bot crash bo‘lmaydi. Logga kanal va target haqida yozadi, foydalanuvchiga esa umumiy xabar chiqadi: `Obuna tekshirishda muammo yuz berdi. Administratorga murojaat qiling.`

## Arxiv kanal va kodlar kanali

Bot kino yuborishda arxiv kanal postini foydalanuvchiga `copy_message` orqali nusxalab beradi.

Admin uchun ishlash tartibi:

1. Arxiv kanalga kinolar joylanadi.
2. Bot arxiv kanalga admin yoki member sifatida qo‘shiladi.
3. Kino qo‘shishda arxiv kanal post linki kiritiladi. Masalan: `https://t.me/kino_arxiv/25`.
4. Kodlar kanali majburiy obuna kanali sifatida admin panel orqali qo‘shiladi.
5. User kodlar kanalidan kino kodini olib botga yuboradi.
6. Bot avval kodlar kanali va boshqa active majburiy kanallarga obunani tekshiradi, keyin arxiv kanaldagi postni userga copy qiladi.

Muhim: arxiv kanal majburiy obuna kanallari ichiga qo‘shilmaydi. Majburiy obuna faqat `channels` jadvalidagi active kodlar kanali yoki admin qo‘shgan promo kanallar uchun ishlaydi.

## Admin panel

Admin panel:

```text
/admin
```

Mavjud bo‘limlar:

- `➕ Kino qo‘shish`
- `📋 Kinolar ro‘yxati`
- `🗑 Kino o‘chirish`
- `📢 Kanal qo‘shish`
- `📋 Kanallar ro‘yxati`
- `❌ Kanal o‘chirish`
- `📊 Statistika`
- `📨 Reklama yuborish`

Admin bo‘lmagan foydalanuvchilar `/admin` paneliga kira olmaydi.

## Database

Database modellari:

- `users`: Telegram foydalanuvchilari, oxirgi aktivlik, blok holati, oxirgi kino kodi.
- `movies`: kino kodi, nomi, tavsif, fallback link, arxiv kanal `chat_id`, arxiv post `message_id` va ko‘rishlar soni.
- `channels`: majburiy obuna kanallari (`id`, `title`, `username`, `chat_id`, `invite_link`, `is_private`, `is_active`, `created_at`).
- `searches`: foydalanuvchi qidiruvlari va topilgan kino bilan bog‘lanish.

SQLite default ishlatiladi:

```env
DATABASE_URL=sqlite+aiosqlite:///bot.db
```

PostgreSQL uchun `DATABASE_URL` qiymatini async SQLAlchemy driver bilan yozing:

```env
DATABASE_URL=postgresql+asyncpg://user:password@localhost:5432/db_name
```

Masalan:

```env
DATABASE_URL=postgresql+asyncpg://postgres:password@127.0.0.1:5432/bot_db
```

`asyncpg` dependency `requirements.txt` ichida bor. Alohida o‘rnatish kerak bo‘lsa:

```bash
pip install asyncpg
```

SQLite’dan PostgreSQL’ga o‘tishda `bot.db` ichidagi eski ma’lumotlar avtomatik ko‘chirilmaydi. PostgreSQL’da jadvallar `python bot.py` ishga tushganda avtomatik yaratiladi.

## PostgreSQL sozlash

Local PostgreSQL uchun:

1. PostgreSQL serverni o‘rnating va ishga tushiring.
2. Database yarating, masalan `kinobot`.
3. `.env` ichida `DATABASE_URL`ni PostgreSQL URLga almashtiring.

```env
BOT_TOKEN=your_bot_token_here
ADMIN_IDS=123456789,987654321
DATABASE_URL=postgresql+asyncpg://postgres:password@127.0.0.1:5432/bot_db
```

Render yoki Railway PostgreSQL uchun:

1. Platformada PostgreSQL database yarating.
2. Berilgan internal yoki external connection URLni oling.
3. URL `postgres://...` ko‘rinishida bo‘lsa, bot uchun `postgresql+asyncpg://...` ko‘rinishiga almashtiring.

```env
BOT_TOKEN=your_bot_token_here
ADMIN_IDS=123456789,987654321
DATABASE_URL=postgresql+asyncpg://user:password@host:5432/database
```

PostgreSQL URL noto‘g‘ri bo‘lsa yoki serverga ulanib bo‘lmasa, bot logda tushunarli xabar chiqaradi. Eng ko‘p uchraydigan sabablar: `asyncpg` driver yozilmagan, user/password noto‘g‘ri, database yaratilmagan, host/port noto‘g‘ri yoki server public ulanishga ruxsat bermagan.

## Loyiha strukturasi

```text
project/
├── bot.py
├── .env.example
├── requirements.txt
├── README.md
└── app/
    ├── config.py
    ├── database.py
    ├── handlers/
    ├── keyboards/
    ├── models/
    ├── services/
    └── states/
```

## Foydali eslatmalar

- Kino kodi unique saqlanadi.
- Reklama yuborishda botni bloklagan userlar sabab jarayon to‘xtab qolmaydi.
- `/cancel` komandasi admin FSM jarayonini bekor qiladi.
- Bot restart bo‘lsa ham foydalanuvchilar, kinolar, kanallar va qidiruvlar database’da saqlanadi.
