"""
السجل المتكامل للبوت (منفصل عن الإشارات ولا يؤثر عليها).
يكتب في مجلد logs بجانب bot.py ثلاثة ملفات CSV تفتح في Excel أو Google Sheets:

  trades.csv  -> صف لكل صفقة: كل المؤشرات وقت المراقبة والدخول، والنتيجة،
                 وأعلى وأدنى سعر وصلته أثناء الصفقة، وما حدث للسعر بعد الخروج.
  events.csv  -> كل حدث: بدء، تحديث العملات، فحص كل 15 دقيقة، مراقبة، رفض مع السبب،
                 دخول، خروج، أخطاء، ورسالة (البوت يعمل).
  rejects.csv -> كل مطرقة وُجدت ولم تصل للدخول، وأي شرط أسقطها مع قيم المؤشرات.
                 (هذا ما نتعلم منه: أي فلتر يرفض فرصاً رابحة وأيها يحمي من الخسارة)

أي خطأ في الكتابة لا يوقف البوت أبداً.
"""
import os, csv, time
from datetime import datetime, timezone, timedelta

try:
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo(os.environ.get('TIMEZONE', 'Africa/Khartoum'))
except Exception:
    TZ = timezone(timedelta(hours=2))

DIR = 'logs'

TRADE_COLS = [
    'رقم', 'العملة', 'وقت_المراقبة', 'وقت_الدخول', 'وقت_الخروج', 'انتظار_للدخول_د', 'مدة_الصفقة_د',
    'سعر_الدخول', 'سعر_الخروج', 'الهدف', 'الوقف', 'المبلغ_داخل', 'المبلغ_خارج', 'الربح_$', 'الربح_%',
    'النتيجة', 'سبب_الخروج', 'أعلى_سعر_%', 'أدنى_سعر_%',
    'ربح24س_عند_المراقبة', 'ربح24س_عند_الدخول', 'حجم_المطرقة×', 'RSI6_15د', 'RSI6_ساعة', 'RSI6_يومي',
    'مطرقة_ساعة', 'مطرقة_يومي', 'فوق_متوسط20_يومي', 'القوة_نجوم', 'السوق_عند_المراقبة', 'السوق_عند_الدخول',
    'Spread_%', 'حجم_الكسر×', 'قمة_المطرقة', 'قاع_المطرقة',
    'السعر_بعد30د_%', 'السعر_بعد60د_%',
]
EVENT_COLS = ['الوقت', 'الحدث', 'العملة', 'التفاصيل']
REJECT_COLS = ['الوقت', 'العملة', 'سبب_الرفض', 'المرحلة', 'ربح24س', 'حجم_المطرقة×', 'RSI6_15د',
               'RSI6_ساعة', 'RSI6_يومي', 'Spread_%', 'حجم_الكسر×', 'السوق', 'السعر']


def now_str():
    return datetime.now(TZ).strftime('%Y-%m-%d %H:%M:%S')


def _r(x, n=4):
    if isinstance(x, float):
        return round(x, n)
    if isinstance(x, bool):
        return 'نعم' if x else 'لا'
    return x


def _write(name, cols, row):
    try:
        os.makedirs(DIR, exist_ok=True)
        path = os.path.join(DIR, name)
        new = not os.path.exists(path)
        with open(path, 'a', newline='', encoding='utf-8-sig') as f:
            w = csv.writer(f)
            if new:
                w.writerow(cols)
            w.writerow([_r(row.get(c, '')) for c in cols])
    except Exception as e:
        print(f"السجل: تعذّرت الكتابة في {name}: {e}", flush=True)


def event(kind, sym='', **details):
    """حدث عام. kind مثل: بدء، فحص، مراقبة، دخول، خروج، خطأ..."""
    txt = ' | '.join(f"{k}={_r(v)}" for k, v in details.items())
    _write('events.csv', EVENT_COLS, {'الوقت': now_str(), 'الحدث': kind, 'العملة': sym, 'التفاصيل': txt})


def reject(sym, reason, stage, **vals):
    """مطرقة أو مراقبة لم تصل للدخول، مع السبب وقيم المؤشرات."""
    row = {'الوقت': now_str(), 'العملة': sym, 'سبب_الرفض': reason, 'المرحلة': stage}
    row.update(vals)
    _write('rejects.csv', REJECT_COLS, row)


def trade(row):
    """صف كامل لصفقة انتهت."""
    _write('trades.csv', TRADE_COLS, row)


def update_after(trade_no, col, value):
    """يكتب (السعر بعد 30/60 دقيقة من الخروج) في صف الصفقة نفسه."""
    path = os.path.join(DIR, 'trades.csv')
    try:
        with open(path, encoding='utf-8-sig', newline='') as f:
            rows = list(csv.reader(f))
        if not rows:
            return
        head = rows[0]
        ci, ni = head.index(col), head.index('رقم')
        for r in rows[1:]:
            if r and str(r[ni]) == str(trade_no):
                while len(r) < len(head):
                    r.append('')
                r[ci] = _r(value)
        with open(path, 'w', encoding='utf-8-sig', newline='') as f:
            csv.writer(f).writerows(rows)
    except Exception as e:
        print(f"السجل: تعذّر تحديث الصفقة {trade_no}: {e}", flush=True)


def send_all(tg, title='📂 السجل الكامل'):
    """يرسل ملفات السجل الثلاثة إلى تيليجرام."""
    sent = 0
    for name, cap in (('trades.csv', 'الصفقات'), ('rejects.csv', 'المرفوضة وأسبابها'), ('events.csv', 'كل الأحداث')):
        if tg.send_file(os.path.join(DIR, name), f"{title} | {cap}"):
            sent += 1
    if not sent:
        tg.send("📂 السجل فارغ حتى الآن")
