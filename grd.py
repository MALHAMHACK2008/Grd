import os
import threading
import time
import urllib.parse
import uuid
from flask import Flask
import requests
import telebot
from telebot import types

# ----------------------------------------------------
# 0. سيرفر الويب المخصص لـ Render لتفادي توقف الخدمة
# ----------------------------------------------------
web_app = Flask(__name__)

@web_app.route('/')
def home():
    return "Bot is Running 24/7 on Render!"

def run_web():
    port = int(os.environ.get("PORT", 8080))
    web_app.run(host="0.0.0.0", port=port)

threading.Thread(target=run_web, daemon=True).start()

# ----------------------------------------------------
# 1. إعدادات التيليجرام والـ API
# ----------------------------------------------------
BOT_TOKEN = "8932223242:AAGuSuqezywQYlg-cQ-0hj2rMdEiCCta9mc"
MONKEY_HEARTBEAT_URL = "https://monkeybase.hellgems.com/api/admonkey/earn/heartbeat"
ATF_CLAIM_URL = "https://atfminers.asloni.online/miner/index.php"
PAYLOAD = {"mode": "turbo"}

bot = telebot.TeleBot(BOT_TOKEN)
users_mining = {}

# ----------------------------------------------------
# 2. دالة تنفيذ السحب التلقائي لمحفظة الفريق
# ----------------------------------------------------
def execute_atf_claim(token, tg_id):
    current_time_ms = int(time.time() * 1000)
    request_id = str(uuid.uuid4())
    params = {"action": "claim_team_wallet", "t": str(current_time_ms)}
    headers = {
        "authority": "atfminers.asloni.online",
        "accept": "*/*",
        "accept-language": "ar-EG,ar;q=0.9,en-US;q=0.8,en;q=0.7",
        "content-type": "application/json",
        "origin": "https://atfminers.asloni.online",
        "referer": "https://atfminers.asloni.online/miner/index.html?v=1788012819",
        "user-agent": "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Mobile Safari/537.36",
        "x-requested-with": "XMLHttpRequest",
        "x-telegram-init-data": token
    }
    payload = {
        "initData": token,
        "request_id": request_id,
        "device_id": f"dev-{request_id[:18]}",
        "tg_id": str(tg_id)
    }
    try:
        r = requests.post(ATF_CLAIM_URL, params=params, headers=headers, json=payload, timeout=12)
        if r.status_code == 200:
            data = r.json()
            if data.get("status") == "success" or data.get("ok") is True:
                return True, data
            return False, data
        return False, f"HTTP Error {r.status_code}"
    except Exception as e:
        return False, str(e)

# ----------------------------------------------------
# 3. محرك التعدين ومراقبة الرصيد
# ----------------------------------------------------
def mining_thread(user_id):
    while users_mining.get(user_id, {}).get("active", False):
        token = users_mining[user_id]["token"]
        headers = {
            "Host": "monkeybase.hellgems.com",
            "accept": "*/*",
            "content-type": "application/json",
            "user-agent": "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Mobile Safari/537.36",
            "x-telegram-init-data": token,
            "origin": "https://monkey.hellgems.com",
            "referer": "https://monkey.hellgems.com/",
        }
        try:
            res = requests.post(MONKEY_HEARTBEAT_URL, headers=headers, json=PAYLOAD, timeout=10)
            if res.status_code == 200:
                data = res.json()
                users_mining[user_id]["rate"] = data.get("ratePerHour", 0)
                user_data = data.get("current", {}).get("user", {})
                current_usdt = float(user_data.get("usdtBalance", 0.0))
                users_mining[user_id]["usdt"] = current_usdt
                users_mining[user_id]["hits"] += 1
                users_mining[user_id]["status"] = "شغال بنمط Turbo ⚡"

                # فحص شرط السحب عند 0.5
                if current_usdt >= 0.5:
                    success, claim_resp = execute_atf_claim(token, user_id)
                    if success:
                        bot.send_message(
                            user_id,
                            f"🎉 تم السحب التلقائي بنجاح!\nالرصيد: {current_usdt} USDT\nالرد: {claim_resp}"
                        )
            elif res.status_code == 401:
                users_mining[user_id]["status"] = "انتهت صلاحية التوكن"
                users_mining[user_id]["active"] = False
                bot.send_message(user_id, "⚠️ انتهت صلاحية التوكن، أرسل توكناً جديداً للاستمرار.")
                break
            else:
                users_mining[user_id]["status"] = f"خطأ سيرفر {res.status_code}"
        except Exception as e:
            users_mining[user_id]["status"] = f"خطأ: {e}"
        time.sleep(1.5)

def get_menu():
    kb = types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
    kb.add(types.KeyboardButton("▶️ بدء التعدين والسحب"), types.KeyboardButton("⏹️ إيقاف التعدين"))
    kb.add(types.KeyboardButton("📊 رصيدي وحالتي"), types.KeyboardButton("🔑 إدخال / تحديث التوكن"))
    return kb

# ----------------------------------------------------
# 4. أوامر التيليجرام
# ----------------------------------------------------
@bot.message_handler(commands=["start"])
def welcome(msg):
    uid = msg.from_user.id
    if uid not in users_mining:
        users_mining[uid] = {"active": False, "token": None, "usdt": 0.0, "rate": 0.0, "hits": 0, "status": "غير مفعل"}
    bot.send_message(msg.chat.id, "👋 مرحباً بك! اضغط على 🔑 إدخال / تحديث التوكن للبدء.", reply_markup=get_menu())

@bot.message_handler(func=lambda msg: True)
def handle_text(msg):
    uid = msg.from_user.id
    text = msg.text.strip()
    if uid not in users_mining:
        users_mining[uid] = {"active": False, "token": None, "usdt": 0.0, "rate": 0.0, "hits": 0, "status": "غير مفعل"}

    if text == "🔑 إدخال / تحديث التوكن":
        sent = bot.send_message(msg.chat.id, "أرسل التوكن (initData) أو الرابط كاملاً:")
        bot.register_next_step_handler(sent, save_token)
    elif text == "▶️ بدء التعدين والسحب":
        if not users_mining[uid]["token"]:
            bot.send_message(msg.chat.id, "⚠️ أدخل التوكن أولاً عبر الزر المخصص.")
            return
        if not users_mining[uid]["active"]:
            users_mining[uid]["active"] = True
            threading.Thread(target=mining_thread, args=(uid,), daemon=True).start()
            bot.send_message(msg.chat.id, "✅ بدأ التعدين والمراقبة للسحب التلقائي عند 0.5.")
        else:
            bot.send_message(msg.chat.id, "⚡ التعدين قيد التشغيل بالفعل.")
    elif text == "⏹️ إيقاف التعدين":
        users_mining[uid]["active"] = False
        users_mining[uid]["status"] = "متوقف"
        bot.send_message(msg.chat.id, "🛑 تم إيقاف العملية.")
    elif text == "📊 رصيدي وحالتي":
        u = users_mining[uid]
        rep = f"📊 الرصيد: {u['usdt']} USDT\n⚡ الحالة: {u['status']}\n🔄 النبضات: {u['hits']}"
        bot.send_message(msg.chat.id, rep)

def save_token(msg):
    uid = msg.from_user.id
    data = msg.text.strip()
    if "#tgWebAppData=" in data:
        token = urllib.parse.unquote(data.split("#tgWebAppData=")[1].split("&")[0])
    else:
        token = data
    if "query_id=" in token or "user=" in token:
        users_mining[uid]["token"] = token
        bot.send_message(msg.chat.id, "✅ تم حفظ التوكن! اضغط الآن على ▶️ بدء التعدين والسحب.")
    else:
        bot.send_message(msg.chat.id, "❌ التوكن غير صالح، يرجى التأكد وإعادة المحاولة.")

if __name__ == "__main__":
    while True:
        try:
            bot.remove_webhook()
            time.sleep(1)
            bot.infinity_polling(skip_pending=True, timeout=20)
        except Exception:
            time.sleep(3)
