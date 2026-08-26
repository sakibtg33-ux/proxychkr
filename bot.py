import os
import logging
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import asyncio
import aiohttp
from aiohttp_socks import ProxyConnector
from aiohttp import ClientTimeout
import io

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
PORT = int(os.getenv("PORT", 8080))

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ================== হেলথ চেক সার্ভার ==================
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Proxy Bot is running!")

    def log_message(self, format, *args):
        pass  # লগ ফাঁকা রাখি

def run_health_server():
    server = HTTPServer(('0.0.0.0', PORT), HealthCheckHandler)
    logger.info(f"✅ Health check server running on port {PORT}")
    server.serve_forever()

# ================== বট হ্যান্ডলার ==================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🚀 Proxy Checker Bot\n\n"
        "1️⃣ একটি proxy.txt ফাইল পাঠান (প্রতি লাইনে একটি প্রোক্সি)।\n"
        "2️⃣ /chk কমান্ড দিন।\n"
        "3️⃣ বৈধ প্রোক্সিগুলো ফাইল আকারে পাবেন।\n\n"
        "সাপোর্টেড ফরম্যাট:\n"
        "- host:port:user:pass\n"
        "- host:port\n"
        "- http://host:port\n"
        "- socks5://host:port"
    )

async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    doc = update.message.document
    if not doc.file_name.endswith('.txt'):
        await update.message.reply_text("❌ শুধুমাত্র .txt ফাইল পাঠান।")
        return

    file = await doc.get_file()
    content = await file.download_as_bytearray()
    lines = content.decode('utf-8').splitlines()
    proxies = [line.strip() for line in lines if line.strip() and not line.startswith('#')]

    if not proxies:
        await update.message.reply_text("❌ ফাইলটি খালি বা ভুল ফরম্যাট।")
        return

    context.user_data['proxies'] = proxies
    await update.message.reply_text(f"✅ {len(proxies)}টি প্রোক্সি লোড হয়েছে। এখন /chk দিন চেক করার জন্য।")

# ---------- প্রোক্সি চেক ----------
TEST_URL = "http://httpbin.org/ip"
TIMEOUT = 5

async def check_single_proxy(line):
    proxy_url = None
    connector = None

    if line.startswith(('http://', 'https://')):
        proxy_url = line
    elif line.startswith(('socks4://', 'socks5://')):
        connector = ProxyConnector.from_url(line)
    else:
        parts = line.split(':')
        if len(parts) == 4:
            host, port, user, pwd = parts
            proxy_url = f"http://{user}:{pwd}@{host}:{port}"
        elif len(parts) == 2:
            host, port = parts
            proxy_url = f"http://{host}:{port}"
        else:
            return None

    try:
        if connector:
            async with aiohttp.ClientSession(connector=connector, timeout=ClientTimeout(total=TIMEOUT)) as sess:
                async with sess.get(TEST_URL) as resp:
                    if resp.status == 200:
                        return line
        else:
            async with aiohttp.ClientSession(timeout=ClientTimeout(total=TIMEOUT)) as sess:
                async with sess.get(TEST_URL, proxy=proxy_url) as resp:
                    if resp.status == 200:
                        return line
    except:
        pass
    return None

async def check_proxies(update: Update, context: ContextTypes.DEFAULT_TYPE):
    proxies = context.user_data.get('proxies', [])
    if not proxies:
        await update.message.reply_text("⚠️ আগে একটি proxy.txt ফাইল পাঠান।")
        return

    status_msg = await update.message.reply_text(f"🔍 মোট {len(proxies)}টি প্রোক্সি চেক করা হচ্ছে (সুপার ফাস্ট)...")
    sem = asyncio.Semaphore(30)

    async def bounded_check(p):
        async with sem:
            return await check_single_proxy(p)

    tasks = [bounded_check(p) for p in proxies]
    results = await asyncio.gather(*tasks)
    valid = [r for r in results if r]

    await status_msg.delete()

    if valid:
        file_obj = io.StringIO()
        file_obj.write("\n".join(valid))
        file_obj.seek(0)
        await update.message.reply_document(
            document=file_obj,
            filename="valid_proxies.txt",
            caption=f"✅ {len(valid)}টি বৈধ প্রোক্সি পাওয়া গেছে।"
        )
    else:
        await update.message.reply_text("❌ কোনো বৈধ প্রোক্সি পাওয়া যায়নি।")

# ================== মেইন (ইভেন্ট লুপ ঠিক করা) ==================
def main():
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN সেট করা হয়নি! .env ফাইল বা এনভায়রনমেন্ট ভেরিয়েবল চেক করুন।")

    # ১. হেলথ চেক থ্রেড চালু
    health_thread = threading.Thread(target=run_health_server, daemon=True)
    health_thread.start()

    # ২. বট অ্যাপ্লিকেশন তৈরি
    application = Application.builder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("chk", check_proxies))
    application.add_handler(MessageHandler(filters.Document.FileExtension("txt"), handle_file))

    # ৩. ম্যানুয়ালি ইভেন্ট লুপ তৈরি ও সেট করা (Python 3.14-এর জন্য)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        logger.info("🚀 Bot is starting polling...")
        application.run_polling()
    finally:
        loop.close()

if __name__ == "__main__":
    main()
