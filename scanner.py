"""
scanner.py — ماسح السوق (Binance Spot)
----------------------------------------
كل ما يخص البحث في السوق: قراءة الإعدادات، الاتصال بـ Binance، اختيار العملات من كل المنصة
(ربح ≥5% في 24 ساعة + اليومي جيد)، المؤشرات (RSI6، المطرقة اليابانية، الحجم، Spread) ومزاج السوق.
لا يرسل رسائل ولا يدير صفقات؛ هذا عمل bot.py.
"""
import os, sys, json, time, math, itertools
import urllib.request, urllib.parse, urllib.error
from datetime import datetime, timezone, timedelta


# ================= قراءة ملف .env =================
def load_env(name='.env'):
    dirs = []
    try:
        dirs.append(os.path.dirname(os.path.abspath(__file__)))
    except NameError:
        pass
    if sys.argv and sys.argv[0]:
        dirs.append(os.path.dirname(os.path.abspath(sys.argv[0])))
    dirs.append(os.getcwd())
    dirs += [d for d in sys.path if d and os.path.isdir(d)]
    path = None
    for d in dirs:
        for n in (name, 'env', '.env.txt', 'env.txt'):
            if os.path.isfile(os.path.join(d, n)) and os.path.exists(os.path.join(d, 'bot.py')):
                path = os.path.join(d, n)
                break
        if path:
            break
    if not path:
        print("ℹ️ لا يوجد ملف .env: أقرأ الإعدادات من متغيرات البيئة (GitHub Secrets).", flush=True)
        return
    os.chdir(os.path.dirname(path))
    if os.path.dirname(path) not in sys.path:
        sys.path.insert(0, os.path.dirname(path))
    with open(path, encoding='utf-8-sig') as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                k, v = line.split('=', 1)
                # إعدادات GitHub Secrets (متغيرات البيئة) لها الأولوية على ملف .env
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    print(f"✔ قرأت الإعدادات من: {path}", flush=True)


load_env()




# ================= الإعدادات =================
def _num(name, default):
    try:
        return type(default)(os.environ.get(name, default))
    except ValueError:
        return default

TRADE_USDT    = _num('TRADE_USDT', 3.0)              # المبلغ
VOL_MULT      = _num('VOL_MULT', 3.0)                # ارتفاع الحجم لبدء المراقبة (×)
DOWN          = _num('DOWN_PCT', 2.0) / 100          # الهبوط في آخر ساعة لتكون "في هبوط"
ENTRY_VOL     = _num('ENTRY_VOL', 1.5)               # الحجم المطلوب عند الدخول (×)
NET_PROFIT    = _num('NET_PROFIT_PCT', 1.5) / 100    # هدف الربح الصافي
STOP          = _num('STOP_PCT', 2.0) / 100          # وقف الخسارة (0 = بدون)
MAX_MINUTES   = _num('MAX_MINUTES', 60)              # أقصى مدة للصفقة
WATCH_MINUTES = _num('WATCH_MINUTES', 15)            # مدة المراقبة قبل الإلغاء
TOP_COINS     = _num('TOP_COINS', 5000)           # المرحلة 1: أكثر العملات سيولة التي تقبل مبلغك (حتى 200)
TOP_FINAL     = _num('TOP_FINAL', 50)            # المرحلة 2: أفضل عدد منها بحسب سجلّها
MIN_WINRATE   = _num('MIN_WINRATE', 65.0)            # أقل نسبة نجاح تاريخية لتُراقب العملة %
MIN_SAMPLES   = _num('MIN_SAMPLES', 3)               # أقل عدد إشارات سابقة لنثق بالنسبة
RANK_DAYS     = _num('RANK_DAYS', 30)                # الأيام التي نقيس عليها نسبة النجاح
BACKTEST_DAYS = _num('BACKTEST_DAYS', 30)
TEST_DAYS     = _num('TEST_DAYS', 14)                # walkforward: أيام التحقق بعد فترة الاختيار
SLIP          = _num('SLIP_PCT', 0.1) / 100          # انزلاق السعر عند الشراء والبيع اليدوي (في الاختبار)
MAX_CHASE     = _num('MAX_CHASE_PCT', 0.3) / 100     # لا ترسل "ادخل" إذا ابتعد السعر أكثر من هذا فوق إغلاق شمعة الكسر
SCAN_EVERY    = 10        # يفحص السوق كل 10 ثوانٍ
HEARTBEAT_MIN = _num('HEARTBEAT_MIN', 30)           # رسالة (البوت يعمل) كل كم دقيقة
MAX_WATCH     = 3         # أقصى عدد عملات تراقبها معاً
FEE           = 0.001     # رسوم Binance 0.1% لكل عملية
TIMEFRAME     = '5m'
STATE_FILE    = 'signals_state.json'

try:
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo(os.environ.get('TIMEZONE', 'Africa/Khartoum'))
except Exception:
    TZ = timezone(timedelta(hours=2))

STABLES = {'USDC', 'FDUSD', 'TUSD', 'USDP', 'DAI', 'EUR', 'TRY', 'BRL', 'USD1',
           'XUSD', 'USDE', 'BFUSD', 'AEUR', 'PAXG'}


def fmt_p(x):
    return f"{x:.8g}"


def target_price(entry, profit=None):
    """السعر الذي يعطي الربح الصافي المطلوب بعد رسوم الشراء والبيع."""
    profit = NET_PROFIT if profit is None else profit
    return entry * (1 + FEE) * (1 + profit) / (1 - FEE)


def net_value(amount, entry, exit_price):
    """قيمة المبلغ بعد البيع، مع خصم الرسوم."""
    return amount * (exit_price * (1 - FEE)) / (entry * (1 + FEE))


# ================= اتصال Binance (أسعار وشموع حقيقية) =================
class Binance:
    # data-api.binance.vision: بيانات السوق العامة، تعمل أيضاً من خوادم GitHub (أمريكا) التي يحجبها api.binance.com
    BASES = [os.environ.get('BINANCE_BASE', 'https://data-api.binance.vision'), 'https://api.binance.com']

    def __init__(self):
        self.BASE = self.BASES[0]
        self.info = {}
        self.opener = urllib.request.build_opener()
        proxy = os.environ.get('PROXY_URL', '')
        if not proxy:                                   # بروكسي WARP على GitHub: يُكتشف تلقائياً إن كان يعمل
            import socket
            try:
                socket.create_connection(('127.0.0.1', 40000), timeout=1).close()
                proxy = 'http://127.0.0.1:40000'
            except OSError:
                proxy = ''
        if proxy:
            self.opener = urllib.request.build_opener(
                urllib.request.ProxyHandler({'http': proxy, 'https': proxy}))
            print(f"✔ اتصال Binance عبر البروكسي {proxy}", flush=True)
        self.key = os.getenv('BINANCE_API') or os.getenv('BINANCE_KEY', '')

    def _get(self, path, params=None, tries=4):
        url = self.BASE + path + ('?' + urllib.parse.urlencode(params) if params else '')
        headers = {'User-Agent': 'signal-bot'}
        if self.key:
            headers['X-MBX-APIKEY'] = self.key
        last = None
        for k in range(tries):
            try:
                req = urllib.request.Request(url, headers=headers)
                with self.opener.open(req, timeout=30) as r:
                    return json.loads(r.read())
            except urllib.error.HTTPError as e:
                msg = e.read().decode(errors='ignore')[:150]
                if e.code in (418, 429):      # طلبات كثيرة: انتظر أطول
                    time.sleep(5 * (k + 1))
                    last = RuntimeError(f"Binance {e.code}: {msg}")
                    continue
                if e.code in (451, 403) and k < tries - 1:   # الموقع محجوب: جرّب العنوان الآخر
                    self.BASE = self.BASES[1] if self.BASE == self.BASES[0] else self.BASES[0]
                    url = self.BASE + path + ('?' + urllib.parse.urlencode(params) if params else '')
                    last = RuntimeError(f"Binance {e.code}: {msg}")
                    continue
                raise RuntimeError(f"Binance {e.code}: {msg}")
            except Exception as e:            # انقطاع الشبكة / انتهاء المهلة
                last = e
                if k == 1:                    # بعد فشلين: جرّب العنوان الآخر
                    self.BASE = self.BASES[1] if self.BASE == self.BASES[0] else self.BASES[0]
                    url = self.BASE + path + ('?' + urllib.parse.urlencode(params) if params else '')
                time.sleep(2 * (k + 1))
        raise RuntimeError(f"فشل الاتصال بعد {tries} محاولات: {last}")

    def load_markets(self):
        for m in self._get('/api/v3/exchangeInfo', {'permissions': 'SPOT'})['symbols']:
            f = {x['filterType']: x for x in m['filters']}
            mn = f.get('NOTIONAL', {}).get('minNotional') or f.get('MIN_NOTIONAL', {}).get('minNotional')
            self.info[m['symbol']] = {
                'base': m['baseAsset'], 'quote': m['quoteAsset'],
                'active': m['status'] == 'TRADING',
                'tick': float(f['PRICE_FILTER']['tickSize']),
                'min_cost': float(mn) if mn else None,
            }
        return self.info

    def volumes(self):
        data = self._get('/api/v3/ticker/24hr')
        self.change24 = {t['symbol']: float(t['priceChangePercent']) for t in data}   # تغيّر السعر في 24 ساعة %
        return {t['symbol']: float(t['quoteVolume']) for t in data}

    def candles(self, sym, limit=40, start=None, interval=None):
        """[وقت, فتح, أعلى, أدنى, إغلاق, حجم]"""
        p = {'symbol': sym, 'interval': interval or TIMEFRAME, 'limit': limit}
        if start:
            p['startTime'] = start
        return [[k[0], float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5])]
                for k in self._get('/api/v3/klines', p)]

    def price(self, sym):
        return float(self._get('/api/v3/ticker/price', {'symbol': sym})['price'])

    def ceil_price(self, sym, p):
        t = self.info.get(sym, {}).get('tick') or 0
        return math.ceil(p / t - 1e-9) * t if t else p


def pick_coins(ex):
    """أعلى العملات سيولة التي تقبل صفقة بـ TRADE_USDT."""
    markets = ex.load_markets()
    vols = ex.volumes()
    ok, total, low_min, mins = [], 0, 0, {}
    for sym, m in markets.items():
        if not (m['active'] and m['quote'] == 'USDT'):
            continue
        total += 1
        if m['base'] in STABLES or m['base'].endswith(('UP', 'DOWN', 'BULL', 'BEAR')):
            continue
        # يجب أن تبقى قيمة البيع فوق الحد الأدنى حتى بعد الوقف والرسوم، وإلا لن تستطيع البيع
        sell_value = TRADE_USDT * (1 - STOP) * (1 - FEE) * 0.99
        if m['min_cost'] is not None:
            mins[m['min_cost']] = mins.get(m['min_cost'], 0) + 1
        if m['min_cost'] is None or m['min_cost'] > sell_value:
            low_min += 1
            continue
        ok.append((vols.get(sym, 0), sym))
    ok.sort(reverse=True)
    ex.stats = (total, len(ok))                      # (كل أزواج USDT، التي تقبل مبلغك)
    common = max(mins, key=mins.get) if mins else 0
    ex.funnel = {'pairs': total, 'too_small': low_min, 'fit': len(ok), 'common_min': common}
    return [s for _, s in ok[:int(TOP_COINS)]]



# ================= الإشارات المباشرة =================
RSI_15M    = _num('RSI_MAX_15M', 35.0)   # RSI6 على 15د عند المطرقة: تشبّع بيعي (ارتداد)
RSI_1H     = _num('RSI_MAX_1H', 70.0)    # RSI6 على الساعة: ليست متشبعة شراءً
RSI_1D     = _num('RSI_MAX_1D', 90.0)    # RSI6 على اليومي: العملة ليست محترقة
MIN_24H    = _num('MIN_24H_PCT', 5.0)    # العملة مقبولة فقط إذا ربحت هذه النسبة أو أكثر في آخر 24 ساعة (زخم)
MAX_SPREAD = _num('MAX_SPREAD_PCT', 0.2) / 100   # أقصى فرق بين سعر الشراء والبيع
BRK_VOL    = _num('BREAK_VOL', 1.0)      # حجم شمعة الكسر (متوقعاً لنهايتها) ÷ متوسط آخر 20 شمعة
HAMMER_VOL = _num('HAMMER_VOL', 1.2)     # حجم شمعة المطرقة ÷ متوسط آخر 20 شمعة


def rsi(closes, n=6):
    """RSI (طريقة وايلدر). يرجع قائمة بطول الإغلاقات."""
    out = [50.0] * len(closes)
    if len(closes) <= n:
        return out
    gains = [max(closes[k] - closes[k - 1], 0) for k in range(1, len(closes))]
    losses = [max(closes[k - 1] - closes[k], 0) for k in range(1, len(closes))]
    ag, al = sum(gains[:n]) / n, sum(losses[:n]) / n
    for k in range(n, len(closes)):
        if k > n:
            ag = (ag * (n - 1) + gains[k - 1]) / n
            al = (al * (n - 1) + losses[k - 1]) / n
        out[k] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    return out


def is_hammer(cs, i):
    """مطرقة يابانية: ذيل سفلي طويل (≥ ضعف الجسم)، جسم صغير في أعلى الشمعة، وتأتي بعد هبوط."""
    t, o, h, l, c, v = cs[i]
    rng = h - l
    if rng <= 0 or i < 4:
        return False
    body = abs(c - o)
    lower = min(o, c) - l
    upper = h - max(o, c)
    shape = lower >= 2 * max(body, rng * 0.05) and upper <= rng * 0.25 and body <= rng * 0.4
    after_drop = c < cs[i - 4][4] or l < min(x[3] for x in cs[i - 4:i])
    return shape and after_drop


def spread(ex, s):
    """الفرق بين أفضل سعر بيع وأفضل سعر شراء ÷ السعر."""
    b = ex._get('/api/v3/ticker/bookTicker', {'symbol': s})
    bid, ask = float(b['bidPrice']), float(b['askPrice'])
    return (ask - bid) / ask if ask > 0 else 1


LINE = "━━━━━━━━━━━━━━━━━━━━"


def N(x):
    """يعزل الأرقام لتظهر صحيحة داخل النص العربي في تيليجرام."""
    return f"\u2068{x}\u2069"


def rtl(lines):
    return "\n".join(l if l == LINE else "\u200f" + l for l in lines)


def market_state(ex):
    """حالة السوق من كل أزواج USDT: متوسط التغيّر في 24 ساعة ونسبة العملات الصاعدة."""
    try:
        data = ex._get('/api/v3/ticker/24hr')
    except Exception:
        return "غير معروف"
    ch = [float(t['priceChangePercent']) for t in data
          if t['symbol'].endswith('USDT') and float(t['quoteVolume']) > 100_000]
    if not ch:
        return "غير معروف"
    avg = sum(ch) / len(ch)
    up = 100 * sum(1 for x in ch if x > 0) / len(ch)
    if avg >= 3 and up >= 65:
        return "🚀 صاعد بقوة"
    if avg >= 1 and up >= 55:
        return "📈 صاعد"
    if avg <= -3 and up <= 35:
        return "🔻 هابط بقوة"
    if avg <= -1 and up <= 45:
        return "📉 هابط"
    return "↔️ جانبي"


def coin_strength(ch24, vr, r15, hammer_1h, dd):
    """قوة العملة من 5 نجوم: الزخم، حجم المطرقة، عمق التشبّع، تأكيد الساعة، اليومي."""
    score = 1 + (ch24 >= 8) + (vr >= 2) + (r15 <= 25) + bool(hammer_1h or dd.get('hammer'))
    label = "قوية جداً" if score >= 4 else "قوية" if score == 3 else "متوسطة"
    return "⭐" * score, label


def good_coins(ex):
    """يفحص كل عملات المنصة التي تقبل مبلغك: العملة الجيدة على اليومي = فوق متوسط 20 يوماً أو آخر شمعة يومية مطرقة. ثم أفضل TOP_FINAL."""
    all_coins = pick_coins(ex)
    good = []
    ex.daily = {}
    ch = getattr(ex, 'change24', {})
    up, errs, first_err = 0, 0, ''
    for s in all_coins:
        if ch.get(s, 0) < MIN_24H:
            continue                                    # الزخم: لم تربح 5% في 24 ساعة
        up += 1
        try:
            d = ex.candles(s, limit=40, interval='1d')[:-1]     # الشموع اليومية المغلقة
        except Exception as e:
            errs += 1
            first_err = first_err or str(e)[:120]
            continue
        if len(d) < 20:
            continue
        sma = sum(x[4] for x in d[-20:]) / 20
        hammer = is_hammer(d, len(d) - 1)
        r = rsi([x[4] for x in d])[-1]
        if (d[-1][4] > sma or hammer) and r < RSI_1D:
            ex.daily[s] = {'rsi': r, 'hammer': hammer, 'above': d[-1][4] > sma, 'ch': ch.get(s, 0)}
            # الأفضل: الأقوى زخماً في 24 ساعة
            good.append((-ch.get(s, 0), len(good), s))
    good.sort()
    f = getattr(ex, 'funnel', {})
    f.update({'up': up, 'daily_ok': len(good), 'errors': errs, 'first_err': first_err})
    ex.funnel = f
    print(funnel_text(ex), flush=True)
    return all_coins, [s for _, _, s in good[:int(TOP_FINAL)]]


def funnel_text(ex):
    """تشخيص: أين تسقط العملات في كل مرحلة."""
    f = getattr(ex, 'funnel', {})
    t = (f"🔬 تشخيص الاختيار\n"
         f"أزواج USDT: {f.get('pairs', 0)}\n"
         f"تقبل صفقة {TRADE_USDT:g}$: {f.get('fit', 0)} (رُفض {f.get('too_small', 0)}: الحد الأدنى للطلب أكبر من مبلغك، "
         f"والحد الأشيع {f.get('common_min', 0):g}$)\n"
         f"ربحت ≥{MIN_24H:g}% في 24س: {f.get('up', 0)}\n"
         f"اليومي جيد (المقبولة): {f.get('daily_ok', 0)}")
    if f.get('errors'):
        t += f"\n⚠️ أخطاء في جلب الشموع اليومية: {f['errors']} | {f.get('first_err', '')}"
    return t


# ================= إشارات السكنر (يرفعها إلى البوت) =================
def scan_hammers(ex, coins, skip=(), slots=MAX_WATCH, mkt=''):
    """فحص 15 دقيقة: يبحث في العملات المقبولة عن مطرقة تحقق كل الشروط.
    يرجع: (إشارات المراقبة، المرفوضة مع السبب، آخر عملة فُحصت، عدد المطارق)"""
    signals, rejects, last, found = [], [], '', 0
    if 'هابط بقوة' in mkt:
        return signals, rejects, last, found
    for s in coins:
        if s in skip or len(signals) >= slots:
            continue
        last = s
        cs = ex.candles(s, limit=60, interval='15m')[:-1]
        if len(cs) < 30 or not is_hammer(cs, len(cs) - 1):
            continue
        found += 1
        dd = getattr(ex, 'daily', {}).get(s, {})
        r15m = min(rsi([x[4] for x in cs])[-3:])
        avg_v = sum(x[5] for x in cs[-21:-1]) / 20
        vr = cs[-1][5] / avg_v if avg_v > 0 else 0
        vals = {'ربح24س': dd.get('ch'), 'حجم_المطرقة×': vr, 'RSI6_15د': r15m,
                'RSI6_يومي': dd.get('rsi'), 'السوق': mkt, 'السعر': cs[-1][4]}
        if r15m > RSI_15M:
            rejects.append((s, f'RSI6_15د>{RSI_15M:g}', vals))         # لا تشبّع بيعي
            continue
        if vr < HAMMER_VOL:
            rejects.append((s, f'حجم_المطرقة<{HAMMER_VOL:g}', vals))    # مطرقة بلا حجم
            continue
        h1 = ex.candles(s, limit=30, interval='1h')[:-1]
        r1h = rsi([x[4] for x in h1])[-1]
        h1_hammer = is_hammer(h1, len(h1) - 1)
        vals['RSI6_ساعة'] = r1h
        if not (h1_hammer or h1[-1][4] > h1[-1][1]):
            rejects.append((s, 'الساعة_حمراء', vals))                   # الساعة لا تؤكد
            continue
        if r1h >= RSI_1H:
            rejects.append((s, f'RSI6_ساعة≥{RSI_1H:g}', vals))
            continue
        sp = spread(ex, s)
        vals['Spread_%'] = sp * 100
        if sp > MAX_SPREAD:
            rejects.append((s, 'Spread_كبير', vals))                    # فرق سعر يأكل الربح
            continue
        stars, label = coin_strength(dd.get('ch', 0), vr, r15m, h1_hammer, dd)
        signals.append({'sym': s, 'high': cs[-1][2], 'low': cs[-1][3], 'stars': stars, 'label': label,
                        'ch24': dd.get('ch'), 'vr': vr, 'r15': r15m, 'r1h': r1h, 'r1d': dd.get('rsi'),
                        'h1_hammer': h1_hammer, 'd_hammer': dd.get('hammer'), 'd_above': dd.get('above'),
                        'vals': vals})
    return signals, rejects, last, found


def check_entry(ex, s, w):
    """كل 10 ثوانٍ لعملة تحت المراقبة: هل حان الدخول؟
    يرجع dict فيه action: 'enter' ادخل | 'wait' انتظر | 'drop' ألغِ المراقبة | 'none' لا جديد"""
    now = ex.price(s)
    r = {'action': 'none', 'price': now, 'reason': ''}
    if time.time() > w['until']:
        r.update(action='drop', reason='لم_تكسر_خلال_30د' + ('_كسر_ضعيف' if w.get('weak') else ''))
        return r
    if now <= w['high']:
        return r
    if now > w['high'] * (1 + MAX_CHASE + 0.002):
        r.update(action='drop', reason='السعر_هرب_فوق_القمة')
        return r
    # قياس الكسر: هل الكسر بحجم تداول حقيقي؟
    k = ex.candles(s, limit=21, interval='15m')
    avg_v = sum(x[5] for x in k[:-1]) / max(1, len(k) - 1)
    frac = max(0.2, (time.time() * 1000 - k[-1][0]) / 900_000)
    r['brk_vr'] = (k[-1][5] / frac) / avg_v if avg_v > 0 else 0
    if r['brk_vr'] < BRK_VOL:
        r.update(action='wait', reason='كسر_ضعيف')
        return r
    r['ch24'] = float(ex._get('/api/v3/ticker/24hr', {'symbol': s})['priceChangePercent'])
    if r['ch24'] < MIN_24H:
        r.update(action='drop', reason=f'ربح24س<{MIN_24H:g}')            # الشرط الأساسي لحظة الدخول
        return r
    r['spread'] = spread(ex, s)
    if r['spread'] > MAX_SPREAD:
        r.update(action='wait', reason='Spread_اتسع')
        return r
    r['action'] = 'enter'
    return r

