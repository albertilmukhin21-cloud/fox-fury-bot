import asyncio
import logging
from datetime import datetime

from aiogram import Bot, Dispatcher, Router
from aiogram.filters import CommandStart
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
import aiosqlite

# ─── BACKEND API для Mini App ───────────────────────────────────────────────
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
import uvicorn

api_app = FastAPI(title="Fox Fury API")


@api_app.get("/balance/{user_id}")
async def get_balance(user_id: int):
    print(f"Запрос баланса для user_id: {user_id}")
    try:
        row = await get_user_data(user_id)
        print(f"Результат из базы: {row}")  # ← ключевой лог!
        if row is None:
            print("Пользователь не найден в БД")
            raise HTTPException(status_code=404, detail="User not found")

        fur, energy, max_energy, _, _, invited_count, last_bonus_date = row
        print(f"Успешно: FUR={fur}, Energy={energy}, max_energy={max_energy}")
        return {
            "fur": fur,
            "energy": energy,
            "max_energy": max_energy,
            "invited_count": invited_count
        }
    except Exception as e:
        print(f"Критическая ошибка в get_balance: {str(e)}")
        import traceback
        traceback.print_exc()  # ← полный стек-трейс в логах
        raise HTTPException(status_code=500, detail=str(e))


@api_app.post("/tap")
async def tap(request: Request):
    data = await request.json()
    user_id = data.get("user_id")
    if not user_id:
        raise HTTPException(status_code=400, detail="user_id required")

    row = await get_user_data(user_id)
    if row is None:
        raise HTTPException(status_code=404, detail="User not found")

    fur, energy, max_energy, _, _, invited_count, last_bonus_date = row

    if energy < 1:
        return {"success": False, "message": "No energy"}

    new_fur = fur + 1
    new_energy = energy - 1

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE users SET fur = ?, energy = ?, last_active = ? WHERE user_id = ?",
            (new_fur, new_energy, datetime.utcnow(), user_id)
        )
        await db.commit()

    return {
        "success": True,
        "fur": new_fur,
        "energy": new_energy
    }


async def run_api():
    config = uvicorn.Config(app=api_app, host="0.0.0.0", port=8000, log_level="info")
    server = uvicorn.Server(config)
    await server.serve()


# ─── НАСТРОЙКИ ───────────────────────────────────────────────────────────────

BOT_TOKEN = "7968981096:AAEMRYddTnsn83F1lf68gHgLbNbnOgilnjQ"
DB_PATH = "fox_fury.db"

REFERRAL_BONUS = 500  # Сколько FUR дают и пригласившему, и новому

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
)


# ─── БАЗА ДАННЫХ ─────────────────────────────────────────────────────────────

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id              INTEGER PRIMARY KEY,
                username             TEXT,
                fur                  INTEGER DEFAULT 0,
                energy               INTEGER DEFAULT 1000,
                max_energy           INTEGER DEFAULT 1000,
                last_active          TIMESTAMP,
                referrer_id          INTEGER DEFAULT NULL,
                invited_count        INTEGER DEFAULT 0,
                last_bonus_date      TIMESTAMP
            )
        ''')

        # Миграция колонок (если нужно добавить новые)
        migrations = [
            ("invited_count", "INTEGER DEFAULT 0"),
            ("last_bonus_date", "TIMESTAMP")
        ]

        for col_name, col_type in migrations:
            try:
                await db.execute(f"ALTER TABLE users ADD COLUMN {col_name} {col_type}")
                print(f"Добавлена колонка: {col_name}")
            except aiosqlite.OperationalError as e:
                if "duplicate column name" in str(e).lower():
                    pass
                else:
                    raise e

        await db.commit()


async def get_user_data(user_id: int) -> tuple | None:
    """Возвращает: fur, energy, max_energy, referrer_id, invited_count, last_bonus_date"""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """
            SELECT fur, energy, max_energy, referrer_id, invited_count, last_bonus_date 
            FROM users WHERE user_id = ?
            """,
            (user_id,)
        )
        row = await cursor.fetchone()
        return row


async def create_or_update_user(
        user_id: int,
        username: str,
        referrer_id: int | None = None
) -> str:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT 1 FROM users WHERE user_id = ?", (user_id,)
        )
        exists = await cursor.fetchone() is not None

        bonus_text = ""

        if not exists:
            start_fur = 500
            if referrer_id:
                start_fur += REFERRAL_BONUS
                bonus_text = f"\n\nТебе +{REFERRAL_BONUS} FUR за рефералку! 😎"

            await db.execute('''
                INSERT INTO users 
                (user_id, username, fur, energy, max_energy, last_active, referrer_id)
                VALUES (?, ?, ?, 1000, 1000, ?, ?)
            ''', (user_id, username, start_fur, datetime.utcnow(), referrer_id))
            await db.commit()

            if referrer_id:
                await db.execute(
                    """
                    UPDATE users 
                    SET fur = fur + ?, 
                        invited_count = invited_count + 1
                    WHERE user_id = ?
                    """,
                    (REFERRAL_BONUS, referrer_id)
                )
                await db.commit()

        else:
            await db.execute(
                "UPDATE users SET last_active = ? WHERE user_id = ?",
                (datetime.utcnow(), user_id)
            )
            await db.commit()

        return bonus_text


# ─── КЛАВИАТУРА ──────────────────────────────────────────────────────────────

def get_main_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🐾 Запустить Mini App!", web_app={"url": "https://fox-fury-miniapp.vercel.app"})],
        [InlineKeyboardButton(text="Ежедневный бонус 🎁", callback_data="daily_bonus")],
        [InlineKeyboardButton(text="Мой баланс & Статистика", callback_data="stats")],
        [InlineKeyboardButton(text="Пригласить друзей (+бонус)", callback_data="referral")],
        [InlineKeyboardButton(text="Скоро Airdrop 🔥", callback_data="airdrop")],
    ])


# ─── ХЕНДЛЕРЫ ────────────────────────────────────────────────────────────────

router = Router()


@router.message(CommandStart(deep_link=True))
@router.message(CommandStart())
async def cmd_start(message: Message):
    args = message.text.split()
    referrer_id = None

    if len(args) > 1 and args[1].isdigit():
        try:
            ref = int(args[1])
            if ref != message.from_user.id:
                referrer_id = ref
        except ValueError:
            pass

    user_id = message.from_user.id
    username = message.from_user.username or message.from_user.first_name

    bonus_text = await create_or_update_user(user_id, username, referrer_id)

    row = await get_user_data(user_id)
    if row is None:
        await message.answer("Ошибка загрузки данных пользователя 😔")
        return

    fur, energy, max_energy, _, invited_count, last_bonus_date = row

    text = (
        f"Привет, {message.from_user.first_name}! 🦊\n\n"
        f"Добро пожаловать в <b>Fox Fury Tap</b>!\n"
        f"Тапай по хитрой лисе и фарми <b>FUR</b>!\n\n"
        f"Твой баланс: <b>{fur:,}</b> FUR\n"
        f"Энергия: <b>{energy}</b> / {max_energy}\n"
        f"Приглашено друзей: <b>{invited_count}</b>\n\n"
        "Скоро будет airdrop и большой листинг! 🚀"
    )

    if bonus_text:
        text += bonus_text

    await message.answer(text, reply_markup=get_main_keyboard(), parse_mode="HTML")


# ... (остальные хендлеры: daily_bonus, stats, referral, airdrop, tap — оставь как были)

# ─── ЗАПУСК ──────────────────────────────────────────────────────────────────

async def main():
    print("Инициализация базы данных...")
    await init_db()
    print("База данных готова")

    print("Создание бота...")
    bot = Bot(token=BOT_TOKEN)
    print("Бот создан")

    dp = Dispatcher()
    dp.include_router(router)

    print("Запуск API на https://fox-fury-bot.onrender.com")
    asyncio.create_task(run_api())

    print("Запуск polling... (бот теперь должен работать)")
    await dp.start_polling(bot)


if __name__ == "__main__":
    print("Скрипт запущен")
    asyncio.run(main())