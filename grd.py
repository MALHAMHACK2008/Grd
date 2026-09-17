import threading
import time
import urllib.parse
import requests
import telebot
from telebot import types

# --- إعدادات البوت واللعبة ---
BOT_TOKEN = "8808422049:AAETrng6DwoxDSw5459fRyhFFecKUz6JBo4"
URL = "https://monkeybase.hellgems.com/api/admonkey/earn/heartbeat"
PAYLOAD = {"mode": "turbo"}

bot = telebot.TeleBot(BOT_TOKEN)

# تخزين بيانات التعدين لكل مستخدم بشكل منفصل
users_mining = {}


def mining_thread(user_id):
  """خيط التعدين المستمر في الخلفية لكل مستخدم"""
  while users_mining.get(user_id, {}).get("active", False):
    token = users_mining[user_id]["token"]
    headers = {
        "Host": "monkeybase.hellgems.com",
        "accept": "*/*",
        "content-type": "application/json",
        "user-agent": (
            "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like"
            " Gecko) Chrome/137.0.0.0 Mobile Safari/537.36"
        ),
        "x-telegram-init-data": token,
        "origin": "https://monkey.hellgems.com",
        "referer": "https://monkey.hellgems.com/",
    }

    try:
      res = requests.post(URL, headers=headers, json=PAYLOAD, timeout=10)
      if res.status_code == 200:
        data = res.json()
        users_mining[user_id]["rate"] = data.get("ratePerHour", 0)
        user_data = data.get("current", {}).get("user", {})
        users_mining[user_id]["usdt"] = user_data.get("usdtBalance", 0)
        users_mining[user_id]["hits"] += 1
        users_mining[user_id]["status"] = "شغال بنمط Turbo"
      elif res.status_code == 401:
        users_mining[user_id]["status"] = "انتهت صلاحية التوكن"
        users_mining[user_id]["active"] = False
        bot.send_message(
            user_id,
            "⚠️ انتهت صلاحية التوكن الخاص بك، يرجى إرسال توكن جديد للاستمرار.",
        )
        break
      else:
        users_mining[user_id]["status"] = f"خطأ سرفر {res.status_code}"
    except Exception as e:
      users_mining[user_id]["status"] = f"خطأ اتصال: {e}"

    time.sleep(1.5)  # الفاصل الزمني للوصول إلى السرعة القصوى (0.04)


def get_menu():
  """لوحة الأزرار الرئيسية"""
  kb = types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
  kb.add(
      types.KeyboardButton("▶️ بدء التعدين"),
      types.KeyboardButton("⏹️ إيقاف التعدين"),
  )
  kb.add(
      types.KeyboardButton("📊 رصيدي وحالتي"),
      types.KeyboardButton("🔑 إدخال / تحديث التوكن"),
  )
  return kb


@bot.message_handler(commands=["start"])
def welcome(msg):
  uid = msg.from_user.id
  if uid not in users_mining:
    users_mining[uid] = {
        "active": False,
        "token": None,
        "usdt": 0.0,
        "rate": 0.0,
        "hits": 0,
        "status": "غير مفعل",
    }
  bot.send_message(
      msg.chat.id,
      "👋 مرحباً بك في بوت تعدين AdMonkey المشترك!\n\n"
      "للبدء، اضغط على **🔑 إدخال / تحديث التوكن** وأرسل توكن حسابك أو رابط"
      " اللعبة.",
      parse_mode="Markdown",
      reply_markup=get_menu(),
  )


@bot.message_handler(func=lambda msg: True)
def handle_text(msg):
  uid = msg.from_user.id
  text = msg.text.strip()

  if uid not in users_mining:
    users_mining[uid] = {
        "active": False,
        "token": None,
        "usdt": 0.0,
        "rate": 0.0,
        "hits": 0,
        "status": "غير مفعل",
    }

  if text == "🔑 إدخال / تحديث التوكن":
    sent = bot.send_message(
        msg.chat.id,
        "أرسل الآن نص التوكن (`initData`) أو رابط اللعبة كاملاً:",
        parse_mode="Markdown",
    )
    bot.register_next_step_handler(sent, save_token)

  elif text == "▶️ بدء التعدين":
    if not users_mining[uid]["token"]:
      bot.send_message(
          msg.chat.id,
          "⚠️ لم تدخل التوكن بعد! اضغط على '🔑 إدخال / تحديث التوكن' أولاً.",
      )
      return

    if not users_mining[uid]["active"]:
      users_mining[uid]["active"] = True
      t = threading.Thread(target=mining_thread, args=(uid,), daemon=True)
      t.start()
      bot.send_message(
          msg.chat.id, "✅ تم تشغيل التعدين السحابي لحسابك بنجاح!"
      )
    else:
      bot.send_message(msg.chat.id, "⚡ التعدين يعمل بالفعل لحسابك.")

  elif text == "⏹️ إيقاف التعدين":
    if users_mining[uid]["active"]:
      users_mining[uid]["active"] = False
      users_mining[uid]["status"] = "متوقف"
      bot.send_message(msg.chat.id, "🛑 تم إيقاف عملية التعدين.")
    else:
      bot.send_message(msg.chat.id, "ℹ️ التعدين متوقف حالياً.")

  elif text == "📊 رصيدي وحالتي":
    u = users_mining[uid]
    if not u["token"]:
      bot.send_message(
          msg.chat.id,
          "❌ لم تقم بتسجيل التوكن بعد، اضغط على زر '🔑 إدخال / تحديث التوكن'.",
      )
      return

    # إرسال رسالة أولية ثم تحديثها تلقائياً على الشاشة
    status_msg = bot.send_message(
        msg.chat.id, "⏳ جاري بدء عرض التحديث المباشر..."
    )

    # تحديث الرسالة نفسها 15 مرة بمعدل مرة كل ثانيتين (لمدة 30 ثانية)
    for _ in range(15):
      rep = (
          f"📊 **تقرير حسابك الشخصي (مباشر 🟢):**\n"
          f"📡 الحالة: {u['status']}\n"
          f"💰 رصيد USDT: `{u['usdt']}`\n"
          f"⚡ المعدل في الساعة: `{u['rate']}`\n"
          f"🔄 عدد النبضات: `{u['hits']}`\n\n"
          f"_يتحدث تلقائياً على الشاشة..._"
      )
      try:
        bot.edit_message_text(
            rep, msg.chat.id, status_msg.message_id, parse_mode="Markdown"
        )
      except Exception:
        pass
      time.sleep(2)


def save_token(msg):
  uid = msg.from_user.id
  data = msg.text.strip()

  # فك الرابط تلقائياً إذا أرسل رابط اللعبة كاملاً
  if "#tgWebAppData=" in data:
    token_part = data.split("#tgWebAppData=")[1].split("&tgWebAppVersion=")[0]
    token = urllib.parse.unquote(token_part)
  else:
    token = data

  if "query_id=" in token or "user=" in token:
    users_mining[uid]["token"] = token
    bot.send_message(
        msg.chat.id,
        "✅ تم حفظ التوكن بنجاح! اضغط الآن على **▶️ بدء التعدين**.",
        parse_mode="Markdown",
    )
  else:
    bot.send_message(
        msg.chat.id,
        "❌ التوكن أو الرابط غير صالح. يرجى التأكد وإعادة المحاولة.",
    )


if __name__ == "__main__":
  print("[+] البوت قيد التشغيل وجاهز للاستخدام...")
  bot.infinity_polling()
