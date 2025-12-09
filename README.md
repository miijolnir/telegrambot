# PowerOn Lviv Telegram Bot

Бот, який стежить за оновленнями графіків відключень на сайті
[poweron.loe.lviv.ua](https://poweron.loe.lviv.ua/) і надсилає сповіщення
при зміні розкладу для вказаної групи (наприклад, `3.1`).

## Команди

- `/start` — реєстрація чату, коротка інфа
- `/setup <група>` — задати групу, наприклад: `/setup 3.1`
- `/status` — показати поточну групу і останнє збережене повідомлення

Бот періодично (раз на 5 хвилин) звертається до API:

`https://api.loe.lviv.ua/api/menus?page=1&type=photo-grafic`

і якщо текст графіка для твоєї групи змінився — надсилає нове повідомлення.

---

## Локальний запуск

```bash
python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt

export TELEGRAM_BOT_TOKEN="ТВОЙ_ТОКЕН_З_BOTFATHER"
python bot.py
