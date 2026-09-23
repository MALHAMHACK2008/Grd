import os
import re
import time
import uuid
import asyncio
import requests
from telethon import TelegramClient
from telethon.tl.functions.messages import RequestWebViewRequest

# ----------------------------------------------------
# 1. إعدادات حسابك الدائمة في تيليجرام
# ----------------------------------------------------
API_ID = 36791169
API_HASH = "d3965b64eb7e251a915ccd8ce3ee8104"
SESSION_NAME = "malham_session"

# بيانات تطبيق ATF
BOT_USERNAME = "atfminers_bot"
APP_URL = "https://atfminers.asloni.online/miner/index.html"
BASE_URL = "https://atfminers.asloni.online/miner/index.php"

CURRENT_INIT_DATA = None

# ----------------------------------------------------
# 2. استخراج التوكن تلقائياً من تيليجرام
# ----------------------------------------------------
async def fetch_fresh_init_data(client):
    global CURRENT_INIT_DATA
    try:
        bot_entity = await client.get_input_entity(BOT_USERNAME)
        web_view = await client(RequestWebViewRequest(
            peer=bot_entity,
            bot=bot_entity,
            platform="android",
            url=APP_URL
        ))

        raw_url = web_view.url
        if "#tgWebAppData=" in raw_url:
            init_data = raw_url.split("#tgWebAppData=")[1].split("&tgWebAppVersion=")[0]
            import urllib.parse
            clean_init = urllib.parse.unquote(init_data)
            CURRENT_INIT_DATA = clean_init
            print("[+] تم سحب وتجديد التوكن بنجاح من تيليجرام!")
            return clean_init
    except Exception as e:
        print(f"[-] حدث خطأ في سحب التوكن: {e}")
    return None

# ----------------------------------------------------
# 3. محرك الاتصال بـ ATF
# ----------------------------------------------------
def send_miner_request(action, init_data, extra_payload=None):
    current_time_ms = int(time.time() * 1000)
    url = f"{BASE_URL}?action={action}&t={current_time_ms}"

    tg_id = "0"
    match = re.search(r'id%22%3A(\d+)', init_data) or re.search(r'"id":(\d+)', init_data)
    if match:
        tg_id = match.group(1)

    headers = {
        "User-Agent": "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Mobile Safari/537.36",
        "Content-Type": "application/json",
        "Accept": "*/*",
        "Origin": "https://atfminers.asloni.online",
        "Referer": "https://atfminers.asloni.online/miner/index.html",
        "x-telegram-init-data": init_data
    }

    payload = {
        "initData": init_data,
        "device_id": f"dev-{uuid.uuid4()}",
        "request_id": str(uuid.uuid4()),
        "tg_id": str(tg_id)
    }
    if extra_payload:
        payload.update(extra_payload)

    try:
        r = requests.post(url, headers=headers, json=payload, timeout=12)
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        print(f"[-] خطأ أثناء الطلب: {e}")
    return None

# ----------------------------------------------------
# 4. دورة العمل المستمرة
# ----------------------------------------------------
async def main_loop():
    client = TelegramClient(SESSION_NAME, API_ID, API_HASH)
    await client.start()
    print("[*] تم تسجيل الدخول إلى تيليجرام بنجاح.")

    last_token_update = 0

    while True:
        # تجديد التوكن تلقائياً كل 3 ساعات
        if time.time() - last_token_update > 10800 or not CURRENT_INIT_DATA:
            await fetch_fresh_init_data(client)
            last_token_update = time.time()

        if CURRENT_INIT_DATA:
            # تحديث الرصيد
            login_data = send_miner_request("login", CURRENT_INIT_DATA)
            if login_data and login_data.get("status") == "success":
                user = login_data.get("user", {})
                team_balance = float(user.get("team_wallet_balance", 0.0))
                mined_balance = float(user.get("mined_balance", 0.0))
                print(f"[📊] رصيد التعدين: {mined_balance:.4f} | رصيد الفريق: {team_balance:.4f}")

                # السحب التلقائي عند 0.5
                if team_balance >= 0.5:
                    print("[⚡] الرصيد تجاوز 0.5! جاري السحب...")
                    claim_res = send_miner_request("claim_team_wallet", CURRENT_INIT_DATA)
                    if claim_res and claim_res.get("status") == "success":
                        print(f"[✅] تم سحب محفظة الفريق بنجاح: {claim_res}")

                # تسريع
                send_miner_request("activate_boost", CURRENT_INIT_DATA, {"display_preview": 0.01})

        await asyncio.sleep(10)

if __name__ == "__main__":
    asyncio.run(main_loop())
