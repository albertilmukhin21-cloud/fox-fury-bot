import asyncio
import logging
from datetime import datetime, date

from aiogram import Bot, Dispatcher, Router
from aiogram.filters import CommandStart
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
import aiosqlite

# FastAPI
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
import uvicorn

# Настройки
BOT_TOKEN = "7968981096:AAEMRYddTnsn83F1lf68gHgLbNbnOgilnjQ"  # ← твой токен
DB_PATH = "fox_fury.db"

REFERRAL_BONUS = 500

logging.basicConfig(level=logging.INFO)

router = Router()
app = FastAPI(title="Fox Fury API")


# ─── БАЗА ДАННЫХ ─────────────────────────────────────────────────────────────

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                fur INTEGER DEFAULT 0,
                energy INTEGER DEFAULT 1000,
                max_energy INTEGER DEFAULT 1000,
                last_active TIMESTAMP,
                referrer_id INTEGER,
                invited_count INTEGER DEFAULT 0,
                last_bonus_date DATE
            )
        ''')
        await db.commit()


async def get_user_data(user_id: int):
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


async def create_or_update_user(user_id: int, username: str, referrer_id: int = None):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT 1 FROM users WHERE user_id = ?", (user_id,))
        exists = await cursor.fetchone() is not None

        bonus_text = ""

        if not exists:
            start_fur = 500
            if referrer_id:
                start_fur += REFERRAL_BONUS
                bonus_text = f"\n\nТебе +{REFERRAL_BONUS} FUR за рефералку! 😎"

                # Начисляем пригласившему
                await db.execute(
                    "UPDATE users SET fur = fur + ?, invited_count = invited_count + 1 WHERE user_id = ?",
                    (REFERRAL_BONUS, referrer_id)
                )

            await db.execute(
                """
                INSERT INTO users 
                (user_id, username, fur, energy, max_energy, last_active, referrer_id)
                VALUES (?, ?, ?, 1000, 1000, ?, ?)
                """,
                (user_id, username, start_fur, datetime.utcnow(), referrer_id)
            )
            await db.commit()

        else:
            await db.execute("UPDATE users SET last_active = ? WHERE user_id = ?",
                             (datetime.utcnow(), user_id))
            await db.commit()

        return bonus_text


# ─── API ЭНДПОИНТЫ ──────────────────────────────────────────────────────────

@app.get("/balance/{user_id}")
async def get_balance(user_id: int):
    row = await get_user_data(user_id)
    if row is None:
        raise HTTPException(status_code=404, detail="User not found")

    fur, energy, max_energy, _, invited_count, _ = row
    return {
        "fur": fur,
        "energy": energy,
        "max_energy": max_energy,
        "invited_count": invited_count
    }


@app.post("/tap")
async def tap(request: Request):
    data = await request.json()
    user_id = data.get("user_id")
    if not user_id:
        raise HTTPException(status_code=400, detail="user_id required")

    row = await get_user_data(user_id)
    if row is None:
        raise HTTPException(status_code=404, detail="User not found")

    fur, energy, max_energy, _, invited_count, _ = row

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

    return {"success": True, "fur": new_fur, "energy": new_energy}


# ─── КЛАВИАТУРА ──────────────────────────────────────────────────────────────

def get_main_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🐾 Запустить Mini App", web_app={"url": "https://fox-fury-miniapp.vercel.app"})],
        [InlineKeyboardButton(text="Ежедневный бонус 🎁", callback_data="daily_bonus")],
        [InlineKeyboardButton(text="Мой баланс", callback_data="stats")],
        [InlineKeyboardButton(text="Пригласить друзей", callback_data="referral")],
    ])


# ─── ХЕНДЛЕРЫ ────────────────────────────────────────────────────────────────

@router.message(CommandStart(deep_link=True))
@router.message(CommandStart())
async def cmd_start(message: Message):
    args = message.text.split()
    referrer_id = None
    if len(args) > 1 and args[1].isdigit():
        referrer_id = int(args[1])

    user_id = message.from_user.id
    username = message.from_user.username or message.from_user.first_name

    bonus_text = await create_or_update_user(user_id, username, referrer_id)

    row = await get_user_data(user_id)
    fur, energy, max_energy, _, invited_count, _ = row

    text = (
        f"Привет, {message.from_user.first_name}! 🦊\n\n"
        f"Твой баланс: {fur:,} FUR\n"
        f"Энергия: {energy}/{max_energy}\n"
        f"Приглашено друзей: {invited_count}\n\n"
        "Тапай в Mini App и фарми FUR!"
    )

    if bonus_text:
        text += bonus_text

    await message.answer(text, reply_markup=get_main_keyboard())


# ─── MAIN ────────────────────────────────────────────────────────────────────

async def main():
    await init_db()
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()
    dp.include_router(router)

    # Запуск API в фоне
    config = uvicorn.Config(app=app, host="0.0.0.0", port=8000, log_level="info")
    server = uvicorn.Server(config)
    asyncio.create_task(server.serve())

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())