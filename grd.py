import os
import re
import time
import uuid
import random
import logging
import threading
from flask import Flask
import requests
from urllib3.util.retry import Retry
from requests.adapters import HTTPAdapter
import telebot
from telebot import types

# ----------------------------------------------------
# 0. حل مشكلة Port لـ Render (Dummy Web Server)
# ----------------------------------------------------
web_app = Flask(__name__)

@web_app.route('/')
def home():
    return "ATF Bot is running smoothly on Render!"

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    web_app.run(host="0.0.0.0", port=port)

# تشغيل خادم الويب بخيط منفصل لإسكات فحص البورت في Render
threading.Thread(target=run_flask, daemon=True).start()

# ----------------------------------------------------
# 1. إعدادات التيليجرام والمنصة
# ----------------------------------------------------
BOT_TOKEN = os.environ.get("BOT_TOKEN")
if not BOT_TOKEN:
    BOT_TOKEN = "8932223242:AAGLAHEz3mwFlOLkFf39Cx6VatDracRB0Qs"

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")
BASE_URL = "https://atfminers.asloni.online/miner/index.php"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s]: %(message)s",
    datefmt="%H:%M:%S",
)

active_workers = {}
waiting_for_token = set()

# ----------------------------------------------------
# 2. محرك التعدين لكل مستخدم
# ----------------------------------------------------
class AccountWorker:
    def __init__(self, chat_id, init_data):
        self.chat_id = chat_id
        self.init_data = init_data
        self.tg_id = self.extract_user_id(init_data)
        self.device_id = f"dev-{uuid.uuid4()}"
        
        self.is_running = False
        self.stop_event = threading.Event()
        self.thread = None

        self.pool_balance = 0.0
        self.pending_reward = 0.0
        self.miner_level = 0
        self.total_claims = 0
        self.total_boosts = 0
        self.last_status = "جاهز للبدء"
        self.message_id = None
        self.last_rendered_text = ""

        # متغيرات حساب الوقت
        self.last_cycle_sec = 8.5
        self.avg_gain_per_cycle = 0.0100

        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Mobile Safari/537.36",
            "Content-Type": "application/json",
            "Accept": "*/*",
            "Origin": "https://atfminers.asloni.online",
            "Referer": "https://atfminers.asloni.online/miner/index.html",
            "x-telegram-init-data": self.init_data
        })
        retries = Retry(total=3, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504])
        self.session.mount("https://", HTTPAdapter(max_retries=retries))

    def extract_user_id(self, raw_data):
        try:
            match = re.search(r'id%22%3A(\d+)', raw_data) or re.search(r'"id":(\d+)', raw_data)
            if match:
                return int(match.group(1))
        except Exception:
            pass
        return self.chat_id

    def send_req(self, action: str, extra: dict = None):
        url = f"{BASE_URL}?action={action}&t={int(time.time() * 1000)}"
        payload = {
            "initData": self.init_data,
            "device_id": self.device_id,
            "request_id": str(uuid.uuid4()),
            "tg_id": self.tg_id
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
            self.last_cycle_sec = max(8.5, float(res.get("boost_cycle_seconds", 8)) + 0.5)
            self.last_status = f"⚡ تسريع نشط (#{self.total_boosts})"
            return self.last_cycle_sec
        return 8.5

    def get_estimated_time_remaining(self):
        if self.pending_reward >= 1.0:
            return "حان وقت الجمع الآن! ⏳"
        
        needed = 1.0 - self.pending_reward
        if self.avg_gain_per_cycle <= 0:
            return "جاري الحساب..."

        cycles_left = needed / self.avg_gain_per_cycle
        total_seconds = int(cycles_left * self.last_cycle_sec)

        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60
        seconds = total_seconds % 60

        if hours > 0:
            return f"~ {hours} ساعة و {minutes} دقيقة"
        elif minutes > 0:
            return f"~ {minutes} دقيقة و {seconds} ثانية"
        else:
            return f"~ {seconds} ثانية"

    def get_text(self):
        state = "🟢 يعمل تلقائياً" if self.is_running else "🔴 متوقف"
        progress = min(100, int((self.pending_reward / 1.0) * 100))
        bars = int(10 * (progress / 100))
        bar = "█" * bars + "░" * (10 - bars)
        time_left = self.get_estimated_time_remaining() if self.is_running else "البوت متوقف"

        return (
            f"<b>🤖 لوحة تحكم التعدين المباشرة (ATF)</b>\n\n"
            f"• <b>الحالة:</b> {state}\n"
            f"• <b>المستوى:</b> <code>Lv {self.miner_level}</code>\n"
            f"• <b>الرصيد المتاح:</b> <code>{self.pool_balance:.4f} ATF</code>\n\n"
            f"• <b>التقدم نحو 1 عملة:</b>\n"
            f"<code>[{bar}] {progress}%</code>\n"
            f"• <b>المعلق حالياً:</b> <code>{self.pending_reward:.4f} / 1.0 ATF</code>\n"
            f"• <b>⏳ الوقت المتبقي للجمع:</b> <code>{time_left}</code>\n\n"
            f"• <b>مرات الجمع الناجحة:</b> <code>{self.total_claims}</code>\n"
            f"• <b>مرات التسريع:</b> <code>{self.total_boosts}</code>\n"
            f"• <b>النشاط الأخير:</b> <code>{self.last_status}</code>\n"
            f"<i>(هذه الرسالة تتحدث تلقائياً)</i>"
        )

    def get_markup(self):
        markup = types.InlineKeyboardMarkup(row_width=2)
        btn_toggle = types.InlineKeyboardButton("🛑 إيقاف", callback_data="stop") if self.is_running else types.InlineKeyboardButton("🚀 تشغيل", callback_data="start")
        btn_claim = types.InlineKeyboardButton("💰 جمع يدوي الآن", callback_data="claim")
        markup.add(btn_toggle, btn_claim)
        return markup

    def update_ui(self):
        if not self.message_id:
            return
        new_text = self.get_text()
        if new_text == self.last_rendered_text:
            return
        try:
            bot.edit_message_text(
                new_text,
                self.chat_id,
                self.message_id,
                reply_markup=self.get_markup()
            )
            self.last_rendered_text = new_text
        except Exception:
            pass

    def loop(self):
        while not self.stop_event.is_set():
            try:
                login = self.send_req("login")
                if login and login.get("status") == "success":
                    u = login.get("user", {})
                    self.pool_balance = float(u.get("mined_balance", self.pool_balance))
                    self.miner_level = int(u.get("miner_level", self.miner_level))

                cycle_sec = self.boost()

                if self.pending_reward >= 1.0:
                    self.claim_mining_reward()

                self.update_ui()
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
# 3. توجيهات وأوامر التيليجرام
# ----------------------------------------------------
@bot.message_handler(commands=["start"])
def cmd_start(message):
    cid = message.chat.id
    if cid in active_workers:
        w = active_workers[cid]
        sent = bot.send_message(cid, w.get_text(), reply_markup=w.get_markup())
        w.message_id = sent.message_id
    else:
        waiting_for_token.add(cid)
        bot.send_message(
            cid,
            "👋 <b>أهلاً بك في بوت ATF التلقائي!</b>\n\n"
            "أرسل سطر الـ <b>initData</b> الخاص بحسابك المستخرج من اللعبة لبدء التعدين فوراً:"
        )

@bot.message_handler(func=lambda msg: msg.chat.id in waiting_for_token)
def handle_token_input(message):
    cid = message.chat.id
    raw_text = message.text.strip()

    if "#tgWebAppData=" in raw_text:
        raw_text = raw_text.split("#tgWebAppData=")[1].split("&")[0]

    if "query_id=" not in raw_text and "user=" not in raw_text:
        bot.send_message(cid, "❌ هذا السطر غير صحيح، تأكد من نسخه كاملاً وأرسله مرة أخرى:")
        return

    waiting_for_token.remove(cid)
    worker = AccountWorker(chat_id=cid, init_data=raw_text)
    active_workers[cid] = worker

    sent = bot.send_message(cid, worker.get_text(), reply_markup=worker.get_markup())
    worker.message_id = sent.message_id
    worker.start()
    bot.send_message(cid, "🚀 تم ربط الحساب وبدء التعدين والتجميع التلقائي بنجاح!")

@bot.callback_query_handler(func=lambda call: True)
def on_click(call):
    cid = call.message.chat.id
    if cid not in active_workers:
        bot.answer_callback_query(call.id, "الحساب غير مربوط. أرسل /start أولاً.")
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

    w.update_ui()

if __name__ == "__main__":
    bot.infinity_polling(timeout=10, long_polling_timeout=5)
