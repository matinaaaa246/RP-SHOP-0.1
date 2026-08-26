from flask import Flask
from threading import Thread
import os

app = Flask(__name__)

@app.route('/')
def home():
    # وقتی سایت مانیتورینگ به این آدرس سر می‌زند، این پیام را می‌بیند
    return "Bot is running perfectly!"

def run():
    # رندر پورت را به صورت متغیر محیطی می‌دهد. اگر نبود روی 8080 اجرا می‌شود
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    # اجرای سرور در یک Thread جداگانه تا با کدهای ربات تداخل نداشته باشد
    t = Thread(target=run)
    t.start()
