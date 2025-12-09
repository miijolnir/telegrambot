# bot.py
import os
import json
import logging
import re
from typing import Dict, Any, Tuple, List

import asyncio
import requests
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
)

# ---------------- НАЛАШТУВАННЯ ----------------

API_URL = "https://api.loe.lviv.ua/api/menus?page=1&type=photo-grafic"
USERS_FILE = "users.json"
CHECK_INTERVAL_SECONDS = 300  # як часто перевіряти, 300 = 5 хвилин

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


# ---------------- ЗБЕРЕЖЕННЯ КОРИСТУВАЧІВ ----------------

def load_users() -> Dict[str, Any]:
    if not os.path.exists(USERS_FILE):
        return {}
    with open(USERS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_users(users: Dict[str, Any]) -> None:
    with open(USERS_FILE, "w", encoding="utf-8") as f:
        json.dump(users, f, ensure_ascii=False, indent=2)


# ---------------- РОБОТА З API photo-grafic ----------------

def fetch_raw_html() -> str:
    """
    Тягне JSON з API та повертає HTML-розмітку з графіком по групах.
    Спробує спочатку rawhtml/rawHtml, потім rawMobileHtml.
    Додаємо заголовки, щоб виглядати як нормальний браузер.
    """
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0 Safari/537.36"
        ),
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://poweron.loe.lviv.ua/",
        "Connection": "close",
    }

    resp = requests.get(API_URL, headers=headers, timeout=15)
    resp.raise_for_status()
    data = resp.json()

    members: List[Dict[str, Any]] = data.get("hydra:member")
    if not members:
        raise ValueError("API response has no 'hydra:member'")

    raw_html = None

    def pick_html(item: Dict[str, Any]):
        # порядок пріоритету: rawhtml -> rawHtml -> rawMobileHtml
        if item.get("rawhtml"):
            return item["rawhtml"]
        if item.get("rawHtml"):
            return item["rawHtml"]
        if item.get("rawMobileHtml"):
            return item["rawMobileHtml"]
        return None

    # Спочатку шукаємо серед елементів type == 'photo-grafic'
    for m in members:
        if m.get("type") == "photo-grafic":
            for item in m.get("menuItems", []):
                candidate = pick_html(item)
                if candidate:
                    raw_html = candidate
                    break
        if raw_html:
            break

    # Fallback: просто перший item, де є rawhtml/rawHtml/rawMobileHtml
    if not raw_html:
        for m in members:
            for item in m.get("menuItems", []):
                candidate = pick_html(item)
                if candidate:
                    raw_html = candidate
                    break
            if raw_html:
                break

    if not raw_html:
        raise ValueError("Не знайшов rawhtml/rawMobileHtml у відповіді API")

    return raw_html


def html_to_text(raw_html: str) -> str:
    """
    Перетворює HTML з rawhtml/rawMobileHtml на нормальний текст з переносами рядків.
    """
    from html import unescape

    html = unescape(raw_html)

    # перетворюємо </p> та <br> на нові рядки
    html = re.sub(r"(?i)</p\s*>", "\n", html)
    html = re.sub(r"(?i)<br\s*/?>", "\n", html)

    # прибираємо всі інші теги
    text = re.sub(r"<[^>]+>", "", html)

    # чистимо зайві пробіли/порожні рядки
    lines = [line.strip() for line in text.splitlines()]
    lines = [line for line in lines if line]
    return "\n".join(lines)


def parse_schedule_text(full_text: str, group: str) -> Tuple[str, str, str]:
    """
    З повного тексту витягує:
      - дату графіка
      - "інформація станом на"
      - рядок для конкретної групи
    Повертає (date_str, info_str, group_line)
    """
    lines = full_text.splitlines()

    date_str = "?"
    info_str = "?"
    group_line = ""

    for line in lines:
        if "Графік погодинних відключень на" in line:
            m = re.search(r"на\s+(\d{2}\.\d{2}\.\d{4})", line)
            if m:
                date_str = m.group(1)
            else:
                date_str = line.strip()
        elif "Інформація станом на" in line:
            info_str = line.replace("Інформація станом на", "").strip()
        elif f"Група {group}" in line:
            group_line = line.strip()

    if not group_line:
        group_line = f"Група {group}. Даних не знайдено."

    return date_str, info_str, group_line


def build_message(full_text: str, group: str) -> str:
    """
    Формує фінальне повідомлення для Telegram.
    """
    date_str, info_str, group_line = parse_schedule_text(full_text, group)

    msg = (
        f"⚡ Графік погодинних відключень на {date_str}\n"
        f"Інформація станом на {info_str}\n\n"
        f"{group_line}"
    )
    return msg


def get_message_for_group(group: str) -> str:
    """
    Тягне API, перетворює HTML в текст та формує повідомлення для конкретної групи.
    """
    raw_html = fetch_raw_html()
    full_text = html_to_text(raw_html)
    return build_message(full_text, group)


# ---------------- TELEGRAM КОМАНДИ ----------------

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    users = load_users()
    if chat_id not in users:
        users[chat_id] = {"group": None, "last_message": None}
        save_users(users)

    await update.message.reply_text(
        "Привіт! Я бот, який стежить за оновленнями графіків відключень ⚡\n\n"
        "Налаштуй свою групу командою, наприклад:\n"
        "/setup 3.1\n\n"
        "Перевірити поточний збережений стан: /status\n"
        "Подивитися актуальний графік прямо зараз: /now"
    )


async def cmd_now(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Завжди тягне свіжий графік з API для поточної групи
    і надсилає його користувачу, незалежно від last_message.
    """
    chat_id = str(update.effective_chat.id)
    users = load_users()
    user = users.get(chat_id)

    if not user or not user.get("group"):
        await update.message.reply_text(
            "Група ще не налаштована. Використай /setup, наприклад:\n/setup 3.1"
        )
        return

    group = user["group"]

    try:
        # блокуючий HTTP-виклик запускаємо в окремому потоці
        message_text = await asyncio.to_thread(get_message_for_group, group)
    except Exception as e:
        logger.exception("Помилка при отриманні графіка для /now: %s", e)
        await update.message.reply_text(
            f"Не вдалося отримати поточний графік. "
            f"Технічна деталь: {type(e).__name__}: {e}"
        )
        return

    # оновлюємо last_message, щоб job_queue не дублював
    user["last_message"] = message_text
    save_users(users)

    await update.message.reply_text(message_text)


async def cmd_setup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    users = load_users()
    if chat_id not in users:
        users[chat_id] = {"group": None, "last_message": None}

    if not context.args:
        await update.message.reply_text(
            "Вкажи номер групи.\nПриклад:\n/setup 3.1"
        )
        return

    group = context.args[0].strip()
    users[chat_id]["group"] = group
    users[chat_id]["last_message"] = None
    save_users(users)

    await update.message.reply_text(
        f"Групу збережено: {group}\n"
        "Я повідомлю, коли з'явиться або зміниться графік для цієї групи."
    )


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    users = load_users()
    user = users.get(chat_id)

    if not user or not user.get("group"):
        await update.message.reply_text(
            "Група ще не налаштована. Використай /setup, наприклад:\n/setup 3.1"
        )
        return

    group = user["group"]
    last_message = user.get("last_message")

    # Якщо last_message ще нема – спробуємо підтягнути поточний графік
    if not last_message:
        try:
            last_message = await asyncio.to_thread(get_message_for_group, group)
            user["last_message"] = last_message
            save_users(users)
        except Exception as e:
            logger.exception("Помилка при отриманні графіка для /status: %s", e)
            await update.message.reply_text(
                f"Твоя група: {group}\n\n"
                "Повідомлень ще немає, і не вдалося отримати поточний графік.\n"
                f"Технічна деталь: {type(e).__name__}: {e}"
            )
            return

    msg = f"Твоя група: {group}\n\nОстаннє відоме повідомлення:\n\n{last_message}"
    await update.message.reply_text(msg)


# ---------------- JOBQUEUE: ПЕРІОДИЧНА ПЕРЕВІРКА ----------------

async def job_check_all(context: ContextTypes.DEFAULT_TYPE):
    """
    JobQueue callback: перевіряє всі налаштовані групи для всіх користувачів,
    і якщо текст для групи змінився — шле оновлення.
    """
    users = load_users()
    if not users:
        return

    for chat_id, info in users.items():
        group = info.get("group")
        if not group:
            continue

        try:
            message_text = await asyncio.to_thread(get_message_for_group, group)
        except Exception as e:
            logger.exception("Помилка при отриманні графіка для групи %s: %s", group, e)
            continue

        if message_text != info.get("last_message"):
            info["last_message"] = message_text
            save_users(users)

            try:
                await context.bot.send_message(
                    chat_id=int(chat_id),
                    text=message_text,
                )
                logger.info("Надіслано оновлення для chat_id=%s, group=%s", chat_id, group)
            except Exception as e:
                logger.exception("Не вдалося надіслати повідомлення chat_id=%s: %s", chat_id, e)


# ---------------- ЗАПУСК БОТА ----------------

def main() -> None:
    if not BOT_TOKEN:
        raise RuntimeError("Не задано TELEGRAM_BOT_TOKEN у змінних середовища.")

    application = ApplicationBuilder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CommandHandler("setup", cmd_setup))
    application.add_handler(CommandHandler("status", cmd_status))
    application.add_handler(CommandHandler("now", cmd_now))

    application.job_queue.run_repeating(
        job_check_all,
        interval=CHECK_INTERVAL_SECONDS,
        first=5,
    )

    logger.info("Бот стартує...")
    application.run_polling()


if __name__ == "__main__":
    main()
