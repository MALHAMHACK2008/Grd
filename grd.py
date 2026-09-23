import os
import re
import time
import uuid
import json
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
from telethon.errors import SessionPasswordNeededError

# ----------------------------------------------------
# 0. خادم ويب لإبقاء السيرفر نشطاً على Render / Koyeb
# ----------------------------------------------------
web_app = Flask(__name__)

@web_app.route('/')
def home():
    return "ATF Engine Multi-User is Running 24/7!"

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    web_app.run(host="0.0.0.0", port=port)

threading.Thread(target=run_flask, daemon=True).start()

# ----------------------------------------------------
# 1. إعدادات التيليجرام والتوكن الجديد
# ----------------------------------------------------
API_ID = 36791169
API_HASH = "d3965b64eb7e251a915ccd8ce3ee8104"
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8932223242:AAF9AoozwvbipoKbIcjq2EprAT0CCNfKHH8")

BASE_URL = "https://atfminers.asloni.online/miner/index.php"
DEFAULT_APP_URL = "https://atfminers.asloni.online/miner/index.html"
DEFAULT_TARGET_BOT = "ATF_AIRDROP_bot"

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

# ----------------------------------------------------
# 2. ملف حفظ الجلسات المستقلة لكل مستخدم
# ----------------------------------------------------
SESSIONS_FILE = "user_sessions.json"

def load_user_sessions():
    if os.path.exists(SESSIONS_FILE):
        try:
            with open(SESSIONS_FILE, "r") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_user_session(cid, session_str):
    sessions = load_user_sessions()
    sessions[str(cid)] = session_str
    try:
        with open(SESSIONS_FILE, "w") as f:
            json.dump(sessions, f)
    except Exception as e:
        logging.error(f"فشل حفظ الجلسة: {e}")

saved_user_sessions = load_user_sessions()

telethon_loop = asyncio.new_event_loop()

def start_telethon_loop(loop):
    asyncio.set_event_loop(loop)
    loop.run_forever()

threading.Thread(target=start_telethon_loop, args=(telethon_loop,), daemon=True).start()

active_auth_clients = {}
active_workers = {}
user_login_flows = {}
waiting_target_bot = set()
waiting_manual_token = set()

# ----------------------------------------------------
# 3. إدارة تسجيل الدخول وتثبيت الاتصال (حل مشكلة انتهاء الكود)
# ----------------------------------------------------
class TelethonManager:
    @staticmethod
    def run_coro(coro):
        future = asyncio.run_coroutine_threadsafe(coro, telethon_loop)
        return future.result(timeout=40.0)

    @classmethod
    def send_code(cls, cid, phone_number):
        async def _send():
            if cid in active_auth_clients:
                try:
                    await active_auth_clients[cid].disconnect()
                except Exception:
                    pass

            session_name = f"auth_{cid}"
            client = TelegramClient(session_name, API_ID, API_HASH, loop=telethon_loop)
            await client.connect()
            res = await client.send_code_request(phone_number)
            active_auth_clients[cid] = client
            return res.phone_code_hash
        return cls.run_coro(_send())

    @classmethod
    def sign_in(cls, cid, phone, phone_code_hash, code):
        async def _sign():
            client = active_auth_clients.get(cid)
            if not client or not client.is_connected():
                return "EXPIRED_SESSION", "انقطع الاتصال بالسيرفر، يرجى إعادة طلب الرمز مجدداً."
            try:
                await client.sign_in(phone=phone, code=code, phone_code_hash=phone_code_hash)
                final_session = StringSession.save(client.session)
                await client.disconnect()
                
                if os.path.exists(f"auth_{cid}.session"):
                    os.remove(f"auth_{cid}.session")
                if cid in active_auth_clients:
                    del active_auth_clients[cid]
                    
                return "SUCCESS", final_session
            except SessionPasswordNeededError:
                return "2FA_REQUIRED", ""
            except Exception as e:
                await client.disconnect()
                if os.path.exists(f"auth_{cid}.session"):
                    try:
                        os.remove(f"auth_{cid}.session")
                    except Exception:
                        pass
                if cid in active_auth_clients:
                    del active_auth_clients[cid]
                return "ERROR", str(e)
        return cls.run_coro(_sign())

    @classmethod
    def sign_in_password(cls, cid, password):
        async def _sign_2fa():
            client = active_auth_clients.get(cid)
            if not client or not client.is_connected():
                return "EXPIRED_SESSION", "انقطع الاتصال، يرجى المحاولة من البداية."
            try:
                await client.sign_in(password=password)
                final_session = StringSession.save(client.session)
                await client.disconnect()
                
                if os.path.exists(f"auth_{cid}.session"):
                    os.remove(f"auth_{cid}.session")
                if cid in active_auth_clients:
                    del active_auth_clients[cid]
                    
                return "SUCCESS", final_session
            except Exception as e:
                await client.disconnect()
                if os.path.exists(f"auth_{cid}.session"):
                    try:
                        os.remove(f"auth_{cid}.session")
                    except Exception:
                        pass
                if cid in active_auth_clients:
                    del active_auth_clients[cid]
                return "ERROR", str(e)
        return cls.run_coro(_sign_2fa())

    @classmethod
    def fetch_token(cls, session_str, target_bot=DEFAULT_TARGET_BOT, app_url=DEFAULT_APP_URL):
        target_clean = target_bot.replace("@", "").strip()

        async def _fetch():
            client = TelegramClient(StringSession(session_str), API_ID, API_HASH, loop=telethon_loop)
            try:
                await client.connect()
                if not await client.is_user_authorized():
                    await client.disconnect()
                    return None, "الجلسة منتهية، يرجى إعادة تسجيل الدخول بالرقم."

                bot_entity = await client.get_input_entity(target_clean)
                web_view = await client(RequestWebViewRequest(
                    peer=bot_entity,
                    bot=bot_entity,
                    platform="android",
                    url=app_url
                ))
                raw_url = web_view.url
                await client.disconnect()

                if "#tgWebAppData=" in raw_url:
                    raw_init = raw_url.split("#tgWebAppData=")[1].split("&tgWebAppVersion=")[0].split("&")[0]
                    return urllib.parse.unquote(raw_init), "تم سحب التوكن بنجاح"
                return None, "الرابط لا يحتوي على بيانات initData"
            except Exception as e:
                try:
                    await client.disconnect()
                except Exception:
                    pass
                return None, f"خطأ: {str(e)}"
        return cls.run_coro(_fetch())

# ----------------------------------------------------
# 4. محرك الحساب والتعدين المنفصل لكل مستخدم
# ----------------------------------------------------
class AccountWorker:
    def __init__(self, chat_id):
        self.chat_id = chat_id
        self.string_session = saved_user_sessions.get(str(chat_id))
        self.init_data = None
        self.tg_id = str(chat_id)
        self.device_id = f"dev-{uuid.uuid4()}"
        
        self.is_running = False
        self.stop_event = threading.Event()
        self.thread = None

        self.pool_balance = 0.0
        self.pending_reward = 0.0
        self.miner_level = 0
        self.total_claims = 0
        self.total_boosts = 0
        self.completed_tasks = 0
        self.last_status = "بانتظار تسجيل الدخول أو إدخال التوكن"
        self.message_id = None
        self.last_rendered_text = ""
        self.last_token_time = 0
        self.target_bot = DEFAULT_TARGET_BOT

        self.task_cooldowns = {}
        self.task_start_times = {}
        self.last_cycle_sec = 5.0
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

    def auto_pull_token(self):
        if not self.string_session:
            self.last_status = "⚠️ حسابك غير مسجل، اضغط على 📱 تسجيل بالرقم"
            return False
        token, msg = TelethonManager.fetch_token(self.string_session, self.target_bot)
        if token:
            self.update_headers(token)
            self.last_token_time = time.time()
            self.last_status = f"🔄 تم سحب التوكن من @{self.target_bot} وبدأ التعدين"
            return True
        else:
            self.last_status = f"⚠️ {msg}"
            return False

    def refresh_token_if_needed(self):
        if not self.init_data or (time.time() - self.last_token_time > 7200):
            return self.auto_pull_token()
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
            self.last_status = f"✅ تم جمع 1 عملة (+{claimed} ATF)"
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
            self.last_status = f"⚡ تسريع نشط (#{self.total_boosts})"
        return 5.0

    def process_tasks(self):
        now = int(time.time())
        for task in REPEATABLE_TASKS:
            if self.stop_event.is_set():
                break
            if self.task_cooldowns.get(task, 0) > now:
                continue

            start_timestamp = self.task_start_times.get(task, now - 30)
            claim_payload = {"task_id": task, "client_started_at": start_timestamp}
            c = self.send_req("claim_task", claim_payload)

            if c and c.get("status") == "success":
                rew = c.get("reward", 1)
                self.completed_tasks += 1
                self.pool_balance = float(c.get("new_balance", self.pool_balance + rew))
                self.task_cooldowns[task] = int(c.get("next_available", now + 7200))
                self.last_status = f"🎁 تم جمع مهمة: {task} (+{rew} ATF)"
                self.update_ui()
                self.stop_event.wait(2.0)
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

            self.stop_event.wait(2.0)

    def get_text(self):
        state = "🟢 يعمل تلقائياً" if self.is_running else "🔴 متوقف"
        session_stat = "متصل بنجاح ✅" if self.string_session else "غير مسجل ❌"
        progress = min(100, int((self.pending_reward / 1.0) * 100))
        bars = int(10 * (progress / 100))
        bar = "█" * bars + "░" * (10 - bars)

        return (
            f"<b>🤖 لوحة تحكم مائنر ATF الذكية (تعدد الحسابات)</b>\n\n"
            f"• <b>حالة البوت:</b> {state}\n"
            f"• <b>جلسة التيليجرام:</b> <code>{session_stat}</code>\n"
            f"• <b>البوت المستهدف:</b> <code>@{self.target_bot}</code>\n"
            f"• <b>المستوى:</b> <code>Lv {self.miner_level}</code>\n"
            f"• <b>الرصيد المتاح:</b> <code>{self.pool_balance:.4f} ATF</code>\n\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"<b>💰 تعدين العملات (هدف 1 ATF):</b>\n"
            f"• <b>التقدم:</b> <code>[{bar}] {progress}%</code>\n"
            f"• <b>المعلق:</b> <code>{self.pending_reward:.4f} / 1.0 ATF</code>\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"• <b>المهام المنجزة:</b> <code>{self.completed_tasks}</code>\n"
            f"• <b>مرات الجمع:</b> <code>{self.total_claims}</code> | <b>تسريع:</b> <code>{self.total_boosts}</code> (كل 5 ث)\n"
            f"• <b>آخر نشاط:</b> <code>{self.last_status}</code>\n"
        )

    def get_markup(self):
        markup = types.InlineKeyboardMarkup(row_width=2)
        btn_toggle = types.InlineKeyboardButton("🛑 إيقاف", callback_data="stop") if self.is_running else types.InlineKeyboardButton("🚀 تشغيل", callback_data="start")
        btn_claim = types.InlineKeyboardButton("💰 جمع يدوي", callback_data="claim")
        btn_login = types.InlineKeyboardButton("📱 تسجيل بالرقم", callback_data="tg_login")
        btn_bot = types.InlineKeyboardButton("🎯 سحب من بوت مخصص", callback_data="ask_bot_user")
        btn_pull = types.InlineKeyboardButton("🔄 سحب التوكن الآن", callback_data="pull_now")
        btn_manual = types.InlineKeyboardButton("🔑 إدخال توكن يدوي", callback_data="manual_token")

        markup.add(btn_toggle, btn_claim)
        markup.add(btn_login, btn_pull)
        markup.add(btn_bot, btn_manual)
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
                    self.stop_event.wait(8.0)
                    continue

                login = self.send_req("login")
                if login and login.get("status") == "success":
                    u = login.get("user", {})
                    self.pool_balance = float(u.get("mined_balance", self.pool_balance))
                    self.miner_level = int(u.get("miner_level", self.miner_level))
                    server_cooldowns = login.get("task_cooldowns", {})
                    if server_cooldowns:
                        self.task_cooldowns.update(server_cooldowns)

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
# 5. أوامر البوت والتفاعل
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
        active_workers[cid] = AccountWorker(chat_id=cid)

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
    elif call.data == "pull_now":
        bot.answer_callback_query(call.id, "جاري سحب التوكن...")
        w.auto_pull_token()
    elif call.data == "ask_bot_user":
        waiting_target_bot.add(cid)
        bot.send_message(cid, "🎯 <b>أرسل يوزرنيم البوت الذي تريد سحب التوكن منه:</b>\n(مثال: <code>@ATF_AIRDROP_bot</code>)")
        bot.answer_callback_query(call.id, "بانتظار اسم البوت...")
    elif call.data == "tg_login":
        user_login_flows[cid] = {"step": "WAIT_PHONE"}
        bot.send_message(
            cid,
            "📱 <b>تسجيل حسابك لسحب التوكن والهاش تلقائياً:</b>\n\n"
            "أرسل رقم هاتفك الآن متضمناً مفتاح الدولة الدولي:\n"
            "(مثال: <code>+905123456789</code> أو <code>+963912345678</code>)"
        )
        bot.answer_callback_query(call.id, "بانتظار رقم الهاتف...")
    elif call.data == "manual_token":
        waiting_manual_token.add(cid)
        bot.send_message(cid, "🔑 <b>أرسل سطر الـ initData أو رابط اللعبة كاملاً هنا:</b>")
        bot.answer_callback_query(call.id, "بانتظار التوكن...")

    w.update_ui()

# ----------------------------------------------------
# 6. معالجة المدخلات (الرقم، الكود، 2FA، يوزر البوت، التوكن اليدوي)
# ----------------------------------------------------
@bot.message_handler(func=lambda msg: True)
def handle_all_messages(message):
    cid = message.chat.id
    text = message.text.strip()
    w = active_workers.get(cid)
    if not w:
        w = AccountWorker(chat_id=cid)
        active_workers[cid] = w

    if cid in waiting_manual_token:
        waiting_manual_token.remove(cid)
        raw_text = text
        if "#tgWebAppData=" in raw_text:
            raw_text = urllib.parse.unquote(raw_text.split("#tgWebAppData=")[1].split("&tgWebAppVersion=")[0].split("&")[0])

        if "query_id=" not in raw_text and "user=" not in raw_text:
            bot.send_message(cid, "❌ سطر غير صالح. أرسل الرابط كاملاً أو التوكن الصحيح.")
            return

        w.update_headers(raw_text)
        w.last_status = "⚡ تم تعيين التوكن وبدأ التعدين"
        w.last_token_time = time.time()
        w.update_ui()
        bot.send_message(cid, "✅ تم تعيين التوكن بنجاح! بدأ العمل لحسابك.")
        return

    if cid in waiting_target_bot:
        waiting_target_bot.remove(cid)
        w.target_bot = text.replace("@", "").strip()
        bot.send_message(cid, f"⏳ تم ضبط البوت على <b>@{w.target_bot}</b>، جاري سحب التوكن...")
        if w.auto_pull_token():
            bot.send_message(cid, f"✅ تم سحب التوكن بنجاح من @{w.target_bot} وبدأ التعدين!")
        else:
            bot.send_message(cid, f"⚠️ {w.last_status}")
        w.update_ui()
        return

    if cid in user_login_flows:
        flow = user_login_flows[cid]
        step = flow.get("step")

        if step == "WAIT_PHONE":
            phone = text.replace(" ", "")
            bot.send_message(cid, f"⏳ جاري طلب رمز التحقق للرقم <code>{phone}</code> من تيليجرام...")
            try:
                phone_code_hash = TelethonManager.send_code(cid, phone)
                user_login_flows[cid] = {
                    "step": "WAIT_CODE",
                    "phone": phone,
                    "phone_code_hash": phone_code_hash
                }
                bot.send_message(cid, "📩 <b>وصلك رمز التحقق في التيليجرام!</b>\nأرسل الرمز هنا مباشرة:")
            except Exception as e:
                bot.send_message(cid, f"❌ فشل إرسال الرمز: {e}")
                del user_login_flows[cid]
            return

        elif step == "WAIT_CODE":
            code = text.replace(" ", "").strip()
            phone = flow["phone"]
            phone_code_hash = flow["phone_code_hash"]

            bot.send_message(cid, "⏳ جاري تأكيد الكود وحفظ الجلسة...")
            try:
                status, result = TelethonManager.sign_in(cid, phone, phone_code_hash, code)

                if status == "SUCCESS":
                    save_user_session(cid, result)
                    w.string_session = result
                    del user_login_flows[cid]
                    bot.send_message(
                        cid,
                        "🎉 <b>تم تسجيل الدخول وحفظ جلستك بنجاح!</b>\n\n"
                        "🎯 أرسل الآن يوزرنيم البوت الذي تريد سحب التوكن منه (مثال: <code>@ATF_AIRDROP_bot</code>):"
                    )
                    waiting_target_bot.add(cid)
                    w.update_ui()
                elif status == "2FA_REQUIRED":
                    user_login_flows[cid]["step"] = "WAIT_PASSWORD"
                    bot.send_message(cid, "🔐 <b>حسابك محمي بالتحقق بخطوتين (2FA):</b>\nأرسل كلمة السر الخاصة بحسابك الآن:")
                else:
                    bot.send_message(cid, f"❌ فشل تسجيل الدخول: {result}")
                    del user_login_flows[cid]
            except Exception as e:
                bot.send_message(cid, f"❌ خطأ: {e}")
                del user_login_flows[cid]
            return

        elif step == "WAIT_PASSWORD":
            password = text.strip()
            bot.send_message(cid, "⏳ جاري التحقق من كلمة السر...")
            try:
                status, result = TelethonManager.sign_in_password(cid, password)

                if status == "SUCCESS":
                    save_user_session(cid, result)
                    w.string_session = result
                    del user_login_flows[cid]
                    bot.send_message(
                        cid,
                        "🎉 <b>تم تأكيد كلمة السر وحفظ الجلسة بنجاح!</b>\n\n"
                        "🎯 أرسل الآن يوزرنيم البوت الذي تريد سحب التوكن منه (مثال: <code>@ATF_AIRDROP_bot</code>):"
                    )
                    waiting_target_bot.add(cid)
                    w.update_ui()
                else:
                    bot.send_message(cid, f"❌ كلمة السر غير صحيحة: {result}")
                    del user_login_flows[cid]
            except Exception as e:
                bot.send_message(cid, f"❌ خطأ: {e}")
                del user_login_flows[cid]
            return

# ----------------------------------------------------
# 7. تشغيل محرك البوت مع تنظيف التحديثات المعلقة
# ----------------------------------------------------
if __name__ == "__main__":
    try:
        bot.delete_webhook(drop_pending_updates=True)
        time.sleep(1)
    except Exception as e:
        logging.error(f"Webhook cleanup: {e}")

    while True:
        try:
            bot.infinity_polling(timeout=20, long_polling_timeout=10, skip_pending=True)
        except Exception as e:
            logging.error(f"Polling error: {e}")
            time.sleep(5)
