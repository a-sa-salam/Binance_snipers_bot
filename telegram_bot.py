"""
تيليجرام للبوت: رسائل عند الشراء والبيع والأخطاء، وأمر /status لمعرفة الحالة.

الإعداد (مرة واحدة):
  1) في تيليجرام افتح @BotFather واكتب /newbot وخذ التوكن.
  2) أرسل أي رسالة لبوتك الجديد، ثم افتح في المتصفح:
       https://api.telegram.org/bot<TOKEN>/getUpdates
     وخذ رقم "chat":{"id": ... }
  3) ضعهما كمتغيرات بيئة:
       TELEGRAM_TOKEN=...
       TELEGRAM_CHAT_ID=...
إذا لم تضعهما يعمل البوت عادي بدون تيليجرام.
"""
import os, json, threading, time, uuid, urllib.request, urllib.parse, urllib.error

TOKEN = os.environ.get('TELEGRAM_TOKEN', '')
CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID', '')
ENABLED = bool(TOKEN and CHAT_ID)


def _call(method, params, timeout=35):
    url = f"https://api.telegram.org/bot{TOKEN}/{method}"
    data = urllib.parse.urlencode(params).encode()
    with urllib.request.urlopen(url, data=data, timeout=timeout) as r:
        return json.loads(r.read())


def send(text):
    """يرسل رسالة. يرجع '' إذا نجح، وإلا نص السبب بالعربي. لا يوقف البوت أبداً."""
    if not ENABLED:
        return "TELEGRAM_TOKEN أو TELEGRAM_CHAT_ID فارغ في ملف .env"
    try:
        _call('sendMessage', {'chat_id': CHAT_ID, 'text': text}, timeout=15)
        return ''
    except urllib.error.HTTPError as e:
        try:
            desc = json.loads(e.read()).get('description', '')
        except Exception:
            desc = ''
        if e.code == 401 or e.code == 404:
            why = "التوكن خاطئ. انسخه من BotFather بالكامل (يحتوي على نقطتين :)"
        elif e.code == 403:
            why = "البوت محظور أو لم تضغط Start في محادثته"
        elif e.code == 400 and 'chat not found' in desc:
            why = "لم تبدأ المحادثة مع بوتك (افتحه واضغط Start) أو رقم المحادثة خاطئ"
        else:
            why = f"{e.code} {desc}"
        print(f"تيليجرام: فشل الإرسال: {why}", flush=True)
        return why
    except Exception as e:
        why = f"لا يوجد اتصال بتيليجرام: {e}"
        print(f"تيليجرام: {why}", flush=True)
        return why


def send_file(path, caption=''):
    """يرسل ملفاً (مثل ملفات السجل) إلى محادثتك. لا يوقف البوت أبداً."""
    if not ENABLED or not os.path.exists(path):
        return False
    try:
        b = uuid.uuid4().hex
        name = os.path.basename(path)
        with open(path, 'rb') as f:
            data = f.read()
        body = b''.join([
            f'--{b}\r\nContent-Disposition: form-data; name="chat_id"\r\n\r\n{CHAT_ID}\r\n'.encode(),
            f'--{b}\r\nContent-Disposition: form-data; name="caption"\r\n\r\n{caption}\r\n'.encode(),
            f'--{b}\r\nContent-Disposition: form-data; name="document"; filename="{name}"\r\n'
            f'Content-Type: text/csv\r\n\r\n'.encode(), data, f'\r\n--{b}--\r\n'.encode()])
        req = urllib.request.Request(f"https://api.telegram.org/bot{TOKEN}/sendDocument", data=body,
                                     headers={'Content-Type': f'multipart/form-data; boundary={b}'})
        with urllib.request.urlopen(req, timeout=60) as r:
            r.read()
        return True
    except Exception as e:
        print(f"تيليجرام: فشل إرسال الملف {path}: {e}", flush=True)
        return False


def start_commands(get_status, on_log=None):
    """يستقبل الأوامر في الخلفية. get_status: دالة ترجع نص الحالة. on_log: ترسل ملفات السجل."""
    if not ENABLED:
        return

    def loop():
        offset = 0
        while True:
            try:
                res = _call('getUpdates', {'offset': offset, 'timeout': 30})
                for u in res.get('result', []):
                    offset = u['update_id'] + 1
                    msg = u.get('message') or {}
                    if str(msg.get('chat', {}).get('id')) != str(CHAT_ID):
                        continue  # يتجاهل أي شخص غيرك
                    cmd = (msg.get('text') or '').strip().lower()
                    if cmd in ('/status', '/start', 'حالة'):
                        send(get_status())
                    elif cmd in ('/log', 'سجل', 'السجل') and on_log:
                        on_log()
            except Exception:
                time.sleep(5)

    threading.Thread(target=loop, daemon=True).start()
