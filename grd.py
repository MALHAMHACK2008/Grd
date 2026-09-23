import os
import re
import time
import uuid
import logging
import asyncio
import threading
import urllib.parse
from flask import Flask
import requests
from urllib3.util.retry import Retry
from requests.adapters import HTTPAdapter
import telebot
from telebot import types
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.functions.messages import RequestWebViewRequest

# ----------------------------------------------------
# 0. خادم ويب لإبقاء السيرفر نشطاً على Render 24/7
# ----------------------------------------------------
web_app = Flask(__name__)

@web_app.route('/')
def home():
    return "ATF Engine is Running 24/7!"

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    web_app.run(host="0.0.0.0", port=port)

threading.Thread(target=run_flask, daemon=True).start()

# ----------------------------------------------------
# 1. إعدادات التيليجرام والجلسة النصية
# ----------------------------------------------------
API_ID = 36791169
API_HASH = "d3965b64eb7e251a915ccd8ce3ee8104"
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8932223242:AAGbsCURW1NBWElJGZTMJxCI0EUQxAWKqE4")

# كود الجلسة النصي المستخرج من حسابك
STRING_SESSION = os.environ.get(
    "STRING_SESSION",
    "1BJWap1wBu75aRV8dKyTomYxlTJiCyBZ-QSA_ttAgtplZ6g1OVBmtnWzYJ32uVMADYOD9HYw8XrZsbryA26qjcwQmSMSOgtTKK1HzA3FiNkEpmRTKuoYQF2iTNmwUpBOOOAqbUv3URy3VAIAYFEOh6TiqdJLws8dSbvmX73hH_s7qBVB_OrPw57JmjaZr6X4dfKFDIiZz-ARIuHOzts6xoacy-9eewjMBW5L8keUTQ8PfHqO6f1DescExyPNMW54EfOVDktwCY8wGkkt8DVrRgXj6mE-kYmGgK_Tv9V69Bn_LuVjWsROi0cfTtzABDistDyNZVWeRqJhyXzhZqhpqT98YxT5hbnU="
)

BASE_URL = "https://atfminers.asloni.online/miner/index.php"
DEFAULT_APP_URL = "https://atfminers.asloni.online/miner/index.html"

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")

REPEATABLE_TASKS = [
    "youtube_like_comment",
    "twitter_retweet",
    "website_visit",
    "telegram_react_latest"
]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s]: %(message)s",
    datefmt="%H:%M:%S",
)

active_workers = {}
waiting_bot_username = set()
waiting_manual_token = set()

# ----------------------------------------------------
# 2. استخراج التوكن عبر الجلسة النصية
# ----------------------------------------------------
def fetch_token_from_target_bot(target_bot_username, app_url=DEFAULT_APP_URL):
    target_clean = target_bot_username.replace("@", "").strip()
    
    async def _async_fetch():
        client = TelegramClient(StringSession(STRING_SESSION), API_ID, API_HASH)
        await client.connect()
        if not await client.is_user_authorized():
            await client.disconnect()
            return None, "الجلسة النصية غير مصرحة أو منتهية الصلاحية"

        try:
            bot_entity = await client.get_input_entity(target_clean)
            web_view = await client(RequestWebViewRequest(
                peer=bot_entity,
                bot=bot_entity,
                platform="android",
                url=app_url
            ))
            raw_url = web_view.url
            if "#tgWebAppData=" in raw_url:
                raw_init = raw_url.split("#tgWebAppData=")[1].split("&tgWebAppVersion=")[0].split("&")[0]
                clean_init = urllib.parse.unquote(raw_init)
                await client.disconnect()
                return clean_init, "تم السحب بنجاح"
            await client.disconnect()
            return None, "الرابط المستلم لا يحتوي tgWebAppData"
        except Exception as e:
            await client.disconnect()
            return None, f"خطأ Telethon: {str(e)}"

    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        token, status_msg = loop.run_until_complete(_async_fetch())
        loop.close()
        return token, status_msg
    except Exception as e:
        return None, f"خطأ Loop: {str(e)}"

# ----------------------------------------------------
# 3. محرك الحساب والتعدين
# ----------------------------------------------------
class AccountWorker:
    def __init__(self, chat_id):
        self.chat_id = chat_id
        self.init_data = None
        self.tg_id = str(chat_id)
        self.device_id = f"dev-{uuid.uuid4()}"
        
        self.is_running = False
        self.stop_event = threading.Event()
        self.thread = None

        self.pool_balance = 0.0
        self.pending_reward = 0.0
        self.team_wallet_balance = 0.0
        self.miner_level = 0
        self.total_claims = 0
        self.total_team_claims = 0
        self.total_boosts = 0
        self.completed_tasks = 0
        self.last_status = "جاري الاتصال والتشغيل..."
        self.message_id = None
        self.last_rendered_text = ""
        self.last_token_time = 0
        self.target_bot = "atfminers_bot"

        self.task_cooldowns = {}
        self.task_start_times = {}
        self.last_cycle_sec = 8.5
        self.avg_gain_per_cycle = 0.0100

        self.session = requests.Session()
        retries = Retry(total=3, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504])
        self.session.mount("https://", HTTPAdapter(max_retries=retries))

    def update_headers(self, new_token):
        self.init_data = new_token
        match = re.search(r'id%22%3A(\d+)', new_token) or re.search(r'"id":(\d+)', new_token)
        if match:
            self.tg_id = str(match.group(1))

        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Mobile Safari/537.36",
            "Content-Type": "application/json",
            "Accept": "*/*",
            "Origin": "https://atfminers.asloni.online",
            "Referer": "https://atfminers.asloni.online/miner/index.html",
            "x-telegram-init-data": self.init_data
        })

    def refresh_token_if_needed(self):
        if not self.init_data or (time.time() - self.last_token_time > 9000):
            token, msg = fetch_token_from_target_bot(self.target_bot)
            if token:
                self.update_headers(token)
                self.last_token_time = time.time()
                self.last_status = "🔄 تم سحب التوكن تلقائياً"
                return True
            else:
                if not self.init_data:
                    self.last_status = f"⚠️ {msg}"
                    return False
        return True

    def send_req(self, action: str, extra: dict = None):
        url = f"{BASE_URL}?action={action}&t={int(time.time() * 1000)}"
        payload = {
            "initData": self.init_data,
            "device_id": self.device_id,
            "request_id": str(uuid.uuid4()),
            "tg_id": str(self.tg_id)
        }
        if extra:
            payload.update(extra)
        try:
            r = self.session.post(url, json=payload, timeout=12)
            if r.status_code == 200:
                res = r.json()
                if "tma_session_token" in res:
                    self.session.headers["x-atf-tma-session"] = res["tma_session_token"]
                return res
        except Exception as e:
            logging.error(f"خطأ ({self.chat_id}) عند طلب {action}: {e}")
        return None

    def claim_mining_reward(self):
        payload = {"claim_preview": round(self.pending_reward, 4)}
        res = self.send_req("claim", payload)
        if res and res.get("status") == "success":
            self.total_claims += 1
            claimed = res.get("claimed_amount", 0)
            self.pool_balance = res.get("new_pool_balance", self.pool_balance + claimed)
            self.pending_reward = 0.0
            self.last_status = f"✅ تم جمع التعدين (+{claimed} ATF)"
            return True
        return False

    def claim_team_wallet(self):
        res = self.send_req("claim_team_wallet")
        if res and (res.get("status") == "success" or res.get("ok") is True):
            self.total_team_claims += 1
            claimed = float(res.get("claimed_amount", self.team_wallet_balance))
            self.last_status = f"💸 تم سحب محفظة الفريق (+{claimed:.4f} USDT)"
            self.team_wallet_balance = 0.0
            try:
                bot.send_message(
                    self.chat_id,
                    f"🎉 <b>تم سحب محفظة الفريق تلقائياً!</b>\n"
                    f"💰 القيمة: <code>{claimed:.4f} USDT</code>\n"
                    f"💳 رصيدك وصل للحد الأدنى (0.5) وتم السحب بنجاح."
                )
            except Exception:
                pass
            return True
        return False

    def boost(self):
        old_pending = self.pending_reward
        res = self.send_req("activate_boost", {
            "display_preview": round(self.pending_reward + 0.01, 4)
        })
        if res and res.get("status") == "success":
            self.total_boosts += 1
            new_pending = float(res.get("pending_reward", self.pending_reward))
            gain = new_pending - old_pending
            if gain > 0:
                self.avg_gain_per_cycle = round((self.avg_gain_per_cycle * 0.7) + (gain * 0.3), 5)

            self.pending_reward = new_pending
            self.last_cycle_sec = max(8.5, float(res.get("boost_cycle_seconds", 8)) + 0.5)
            self.last_status = f"⚡ تسريع نشط (#{self.total_boosts})"
            return self.last_cycle_sec
        return 8.5

    def process_tasks(self):
        now = int(time.time())
        for task in REPEATABLE_TASKS:
            if self.stop_event.is_set():
                break
            if self.task_cooldowns.get(task, 0) > now:
                continue

            start_timestamp = self.task_start_times.get(task, now - 30)
            c = self.send_req("claim_task", {"task_id": task, "client_started_at": start_timestamp})
            if c and c.get("status") == "success":
                rew = c.get("reward", 1)
                self.completed_tasks += 1
                self.pool_balance = float(c.get("new_balance", self.pool_balance + rew))
                self.task_cooldowns[task] = int(c.get("next_available", now + 7200))
                self.last_status = f"🎁 تم جمع مهمة: {task} (+{rew} ATF)"
                self.update_ui()
                self.stop_event.wait(3.0)
                continue

            started_at = int(time.time())
            s = self.send_req("start_task", {"task_id": task, "client_started_at": started_at})
            if s and s.get("status") == "success":
                self.task_start_times[task] = started_at
                dur = int(s.get("task_duration", 15))
                self.last_status = f"⏳ جاري تنفيذ: {task}"
                self.update_ui()
                self.stop_event.wait(dur + 2)

                claim_res = self.send_req("claim_task", {"task_id": task, "client_started_at": started_at})
                if claim_res and claim_res.get("status") == "success":
                    rew = claim_res.get("reward", 1)
                    self.completed_tasks += 1
                    self.pool_balance = float(claim_res.get("new_balance", self.pool_balance + rew))
                    self.task_cooldowns[task] = int(claim_res.get("next_available", now + 7200))
                    self.last_status = f"🎁 تم جمع مهمة: {task} (+{rew} ATF)"
                    self.update_ui()

            self.stop_event.wait(3.0)

    def get_text(self):
        state = "🟢 يعمل تلقائياً" if self.is_running else "🔴 متوقف"
        progress = min(100, int((self.pending_reward / 1.0) * 100))
        bars = int(10 * (progress / 100))
        bar = "█" * bars + "░" * (10 - bars)

        return (
            f"<b>🤖 لوحة تحكم مائنر ATF الذكية</b>\n\n"
            f"• <b>الحالة:</b> {state}\n"
            f"• <b>المستوى:</b> <code>Lv {self.miner_level}</code>\n"
            f"• <b>الرصيد المتاح:</b> <code>{self.pool_balance:.4f} ATF</code>\n"
            f"• <b>محفظة الفريق:</b> <code>{self.team_wallet_balance:.4f} USDT</code> (سحب عند 0.5)\n\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"<b>💰 تعدين العملات (هدف 1 ATF):</b>\n"
            f"• <b>التقدم:</b> <code>[{bar}] {progress}%</code>\n"
            f"• <b>المعلق:</b> <code>{self.pending_reward:.4f} / 1.0 ATF</code>\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"• <b>المهام المنجزة:</b> <code>{self.completed_tasks}</code>\n"
            f"• <b>جمع التعدين:</b> <code>{self.total_claims}</code> | <b>سحب الفريق:</b> <code>{self.total_team_claims}</code>\n"
            f"• <b>مرات التسريع:</b> <code>{self.total_boosts}</code>\n"
            f"• <b>البوت المستهدف:</b> <code>@{self.target_bot}</code>\n"
            f"• <b>آخر نشاط:</b> <code>{self.last_status}</code>\n"
        )

    def get_markup(self):
        markup = types.InlineKeyboardMarkup(row_width=2)
        btn_toggle = types.InlineKeyboardButton("🛑 إيقاف", callback_data="stop") if self.is_running else types.InlineKeyboardButton("🚀 تشغيل", callback_data="start")
        btn_claim = types.InlineKeyboardButton("💰 جمع يدوي", callback_data="claim")
        btn_fetch = types.InlineKeyboardButton("🎯 سحب التوكن من يوزر بوت", callback_data="ask_bot_user")
        btn_claim_team = types.InlineKeyboardButton("💸 سحب الفريق الآن", callback_data="claim_team")
        btn_manual = types.InlineKeyboardButton("🔑 إدخال توكن يدوي", callback_data="manual_token")
        
        markup.add(btn_toggle, btn_claim)
        markup.add(btn_fetch)
        markup.add(btn_claim_team, btn_manual)
        return markup

    def update_ui(self):
        if not self.message_id:
            return
        new_text = self.get_text()
        if new_text == self.last_rendered_text:
            return
        try:
            bot.edit_message_text(new_text, self.chat_id, self.message_id, reply_markup=self.get_markup())
            self.last_rendered_text = new_text
        except Exception:
            pass

    def loop(self):
        cycle = 0
        while not self.stop_event.is_set():
            try:
                if not self.refresh_token_if_needed():
                    self.update_ui()
                    self.stop_event.wait(15.0)
                    continue

                login = self.send_req("login")
                if login and login.get("status") == "success":
                    u = login.get("user", {})
                    self.pool_balance = float(u.get("mined_balance", self.pool_balance))
                    self.miner_level = int(u.get("miner_level", self.miner_level))
                    team_bal = float(u.get("team_wallet_balance", login.get("team_wallet_balance", self.team_wallet_balance)))
                    self.team_wallet_balance = team_bal

                    server_cooldowns = login.get("task_cooldowns", {})
                    if server_cooldowns:
                        self.task_cooldowns.update(server_cooldowns)

                if self.team_wallet_balance >= 0.5:
                    self.claim_team_wallet()

                cycle_sec = self.boost()

                if self.pending_reward >= 1.0:
                    self.claim_mining_reward()

                if cycle % 6 == 0:
                    self.process_tasks()

                self.update_ui()
                cycle += 1
                self.stop_event.wait(cycle_sec)
            except Exception as e:
                logging.error(f"خطأ في حلقة {self.chat_id}: {e}")
                self.stop_event.wait(5.0)

    def start(self, message_id=None):
        if message_id:
            self.message_id = message_id
        if not self.is_running:
            self.is_running = True
            self.stop_event.clear()
            self.thread = threading.Thread(target=self.loop, daemon=True)
            self.thread.start()

    def stop(self):
        if self.is_running:
            self.is_running = False
            self.stop_event.set()
            self.update_ui()

# ----------------------------------------------------
# 4. أوامر البوت والتفاعل
# ----------------------------------------------------
@bot.message_handler(commands=["start"])
def cmd_start(message):
    cid = message.chat.id
    if cid not in active_workers:
        worker = AccountWorker(chat_id=cid)
        active_workers[cid] = worker
    else:
        worker = active_workers[cid]

    sent = bot.send_message(cid, worker.get_text(), reply_markup=worker.get_markup())
    worker.message_id = sent.message_id
    worker.start(sent.message_id)

@bot.callback_query_handler(func=lambda call: True)
def on_click(call):
    cid = call.message.chat.id
    if cid not in active_workers:
        bot.answer_callback_query(call.id, "أرسل /start أولاً.")
        return

    w = active_workers[cid]
    w.message_id = call.message.message_id

    if call.data == "start":
        w.start(call.message.message_id)
        bot.answer_callback_query(call.id, "تم البدء 🚀")
    elif call.data == "stop":
        w.stop()
        bot.answer_callback_query(call.id, "تم الإيقاف 🛑")
    elif call.data == "claim":
        w.claim_mining_reward()
        bot.answer_callback_query(call.id, "تم الجمع ✅")
    elif call.data == "claim_team":
        w.claim_team_wallet()
        bot.answer_callback_query(call.id, "جاري طلب سحب محفظة الفريق 💸")
    elif call.data == "ask_bot_user":
        waiting_bot_username.add(cid)
        bot.send_message(cid, "🎯 أرسل الآن يوزرنيم البوت الذي تريد سحب التوكن منه (مثال: <code>@atfminers_bot</code>):")
        bot.answer_callback_query(call.id, "بانتظار معرف البوت...")
    elif call.data == "manual_token":
        waiting_manual_token.add(cid)
        bot.send_message(cid, "أرسل رابط اللعبة كاملاً أو سطر الـ <b>initData</b> هنا:")
        bot.answer_callback_query(call.id, "بانتظار التوكن...")

    w.update_ui()

@bot.message_handler(func=lambda msg: msg.chat.id in waiting_bot_username)
def handle_target_bot(message):
    cid = message.chat.id
    waiting_bot_username.remove(cid)
    target = message.text.strip().replace("@", "")
    
    w = active_workers.get(cid)
    if w:
        w.target_bot = target
        bot.send_message(cid, f"⏳ جاري الاتصال بحسابك عبر الجلسة النصية وسحب توكن <code>@{target}</code>...")
        token, status_msg = fetch_token_from_target_bot(target)
        if token:
            w.update_headers(token)
            w.last_status = f"✅ تم سحب التوكن بنجاح من @{target}"
            w.last_token_time = time.time()
            w.update_ui()
            bot.send_message(cid, f"🎉 <b>تم بنجاح!</b> تم استخراج التوكن وبدأ التعدين على حساب <code>@{target}</code>.")
        else:
            w.last_status = f"❌ تعذر السحب: {status_msg}"
            w.update_ui()
            bot.send_message(cid, f"⚠️ لم يتمكن من سحب التوكن تلقائياً.\nالسبب: <code>{status_msg}</code>\n\nيمكنك استخدام زر <b>إدخال توكن يدوي</b>.")

@bot.message_handler(func=lambda msg: msg.chat.id in waiting_manual_token)
def handle_manual_token(message):
    cid = message.chat.id
    raw_text = message.text.strip()

    if "#tgWebAppData=" in raw_text:
        raw_text = urllib.parse.unquote(raw_text.split("#tgWebAppData=")[1].split("&tgWebAppVersion=")[0].split("&")[0])

    if "query_id=" not in raw_text and "user=" not in raw_text:
        bot.send_message(cid, "❌ سطر غير صالح. أرسل الرابط كاملاً أو التوكن الصحيح:")
        return

    waiting_manual_token.remove(cid)
    w = active_workers.get(cid)
    if w:
        w.update_headers(raw_text)
        w.last_status = "⚡ تم تعيين التوكن يدوياً وبدأ العمل"
        w.last_token_time = time.time()
        w.update_ui()
        bot.send_message(cid, "✅ تم تفعيل التوكن بنجاح! التعدين يعمل الآن.")

# ----------------------------------------------------
# 5. تشغيل البوت وتفادي التعارض
# ----------------------------------------------------
if __name__ == "__main__":
    try:
        bot.remove_webhook()
    except Exception:
        pass

    while True:
        try:
            bot.infinity_polling(timeout=20, long_polling_timeout=10, skip_pending=True)
        except Exception as e:
            logging.error(f"Polling exception: {e}")
            time.sleep(5)
