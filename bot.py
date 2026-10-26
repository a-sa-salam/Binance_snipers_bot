"""
بوت ترشيح - Binance Spot (إشارات فقط، لا يلمس حسابك)
-----------------------------------------------------
الاستراتيجية: المطارق اليابانية على 15 دقيقة + الساعة + اليوم
  0) الزخم: كل عملات المنصة، المقبولة فقط التي ربحت ≥5% في 24 ساعة، والأقوى أولاً، ونأخذ الجيدة (فوق متوسط 20 يوماً أو مطرقة يومية) حتى 50 عملة
  1) 👀 راقب:   مطرقة على 15 دقيقة بعد هبوط + RSI6 ≤ 35 + حجم ≥ ×1.2، والساعة تؤكد (مطرقة أو خضراء، RSI6 < 70)
     واليومي: RSI6 < 80
  2) 🟢 ادخل الآن: خلال 30 دقيقة يكسر السعر قمة المطرقة بحجم تداول قوي. الوقف تحت قاع المطرقة (حتى -2%)
  3) 🔴 اخرج الآن: عند الهدف +1.5% صافي، أو وقف الخسارة، أو انتهاء المدة. وقبلها: ⚠️ استعد للخروج

التشغيل: اضبط MODE في ملف .env
  MODE=signals   -> الإشارات المباشرة في تيليجرام
  MODE=backtest  -> اختبار الاستراتيجية على الأيام الماضية
  MODE=sweep     -> يجرّب عدة إعدادات ويتحقق منها على نصف لم يُستخدم في الاختيار
  MODE=walkforward -> يختبر "اختيار العملات بحسب سجلّها": يختار على 30 يوماً ثم يقيس على 14 يوماً بعدها لم يرها
لا يحتاج أي مكتبات إضافية.
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
        print("⚠️ لم أجد ملف .env بجانب bot.py. ضعه في نفس المجلد.", flush=True)
        return
    os.chdir(os.path.dirname(path))
    if os.path.dirname(path) not in sys.path:
        sys.path.insert(0, os.path.dirname(path))
    with open(path, encoding='utf-8-sig') as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                k, v = line.split('=', 1)
                os.environ[k.strip()] = v.strip().strip('"').strip("'")
    print(f"✔ قرأت الإعدادات من: {path}", flush=True)


load_env()
import telegram_bot as tg
import journal as jr


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


def log(msg, notify=False):
    t = datetime.now(TZ).strftime('%Y-%m-%d %H:%M:%S')
    print(f"[{t}] {msg}", flush=True)
    if notify:
        tg.send(f"{msg}\n🕒 {datetime.now(TZ).strftime('%H:%M:%S')} (الخرطوم)")


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
    BASE = 'https://api.binance.com'

    def __init__(self):
        self.info = {}
        self.key = os.environ.get('BINANCE_KEY', '')

    def _get(self, path, params=None, tries=4):
        url = self.BASE + path + ('?' + urllib.parse.urlencode(params) if params else '')
        headers = {'User-Agent': 'signal-bot'}
        if self.key:
            headers['X-MBX-APIKEY'] = self.key
        last = None
        for k in range(tries):
            try:
                req = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(req, timeout=30) as r:
                    return json.loads(r.read())
            except urllib.error.HTTPError as e:
                msg = e.read().decode(errors='ignore')[:150]
                if e.code in (418, 429):      # طلبات كثيرة: انتظر أطول
                    time.sleep(5 * (k + 1))
                    last = RuntimeError(f"Binance {e.code}: {msg}")
                    continue
                raise RuntimeError(f"Binance {e.code}: {msg}")
            except Exception as e:            # انقطاع الشبكة / انتهاء المهلة
                last = e
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
    ok, total = [], 0
    for sym, m in markets.items():
        if not (m['active'] and m['quote'] == 'USDT'):
            continue
        total += 1
        if m['base'] in STABLES or m['base'].endswith(('UP', 'DOWN', 'BULL', 'BEAR')):
            continue
        # يجب أن تبقى قيمة البيع فوق الحد الأدنى حتى بعد الوقف والرسوم، وإلا لن تستطيع البيع
        sell_value = TRADE_USDT * (1 - STOP) * (1 - FEE) * 0.99
        if m['min_cost'] is None or m['min_cost'] > sell_value:
            continue
        ok.append((vols.get(sym, 0), sym))
    ok.sort(reverse=True)
    ex.stats = (total, len(ok))                      # (كل أزواج USDT، التي تقبل مبلغك)
    return [s for _, s in ok[:int(TOP_COINS)]]


# ================= الاستراتيجية =================
def indicators(cs):
    """لكل شمعة: نسبة الحجم لمتوسط آخر 20، هل أغلقت تحت متوسط 20، التغير في ساعة، خضراء، كسرت قمة آخر شمعتين."""
    n = len(cs)
    ratio, below, chg, green, brk = [0.0] * n, [False] * n, [0.0] * n, [False] * n, [False] * n
    for i in range(n):
        o, c, v = cs[i][1], cs[i][4], cs[i][5]
        green[i] = c > o
        if i >= 2:
            brk[i] = c > max(cs[i - 1][2], cs[i - 2][2])
        if i >= 20:
            avg = sum(x[5] for x in cs[i - 20:i]) / 20
            ratio[i] = v / avg if avg > 0 else 0.0
            below[i] = c < sum(x[4] for x in cs[i - 19:i + 1]) / 20
        if i >= 12:
            chg[i] = c / cs[i - 12][4] - 1
    return {'cs': cs, 'ratio': ratio, 'below': below, 'chg': chg, 'green': green, 'brk': brk}


def watch_ok(d, i, vol=None, down=None):
    vol = VOL_MULT if vol is None else vol
    down = DOWN if down is None else down
    return d['ratio'][i] >= vol and d['below'][i] and d['chg'][i] <= -down


def entry_ok(d, i):
    return d['green'][i] and d['brk'][i] and d['ratio'][i] >= ENTRY_VOL


# ================= الاختبار على البيانات السابقة =================
def fetch_history(ex, sym, days):
    since = int(time.time() * 1000) - days * 86400_000
    out = []
    while True:
        batch = ex.candles(sym, limit=1000, start=since)
        if not batch:
            break
        out += batch
        since = batch[-1][0] + 1
        if len(batch) < 1000:
            break
    return out


def build(data):
    pre = {s: indicators(cs) for s, cs in data.items()}
    by_ts = {}
    for s, d in pre.items():
        for i in range(21, len(d['cs'])):
            by_ts.setdefault(d['cs'][i][0], []).append((s, i))
    return pre, sorted(by_ts.items())


def simulate(pre, timeline, vol=None, down=None, profit=None, stop=None, maxm=None):
    """صفقة واحدة في كل مرة. الدخول عند إغلاق شمعة الكسر. عند تحقق الوقف والهدف في نفس الشمعة نفترض الوقف أولاً."""
    profit = NET_PROFIT if profit is None else profit
    stop = STOP if stop is None else stop
    maxm = MAX_MINUTES if maxm is None else maxm
    max_bars = max(1, int(maxm // 5))
    watch_bars = max(1, int(WATCH_MINUTES // 5))
    T = TRADE_USDT
    pos, watch, results = None, {}, []
    for t, entries in timeline:
        if pos:
            idx = next((i for s, i in entries if s == pos['sym']), None)
            if idx is None:
                continue
            c = pre[pos['sym']]['cs'][idx]
            if pos['stop'] and c[3] <= pos['stop']:
                results.append(net_value(T, pos['entry'], min(c[1], pos['stop']) * (1 - SLIP)) - T)
                pos = None
            elif c[2] >= pos['tp']:
                results.append(T * profit)
                pos = None
            elif idx - pos['i'] >= max_bars:
                results.append(net_value(T, pos['entry'], c[4] * (1 - SLIP)) - T)
                pos = None
            continue
        best = None
        for s, i in entries:
            d = pre[s]
            w = watch.get(s)
            if w is not None and w < i <= w + watch_bars:
                if entry_ok(d, i) and (not best or d['ratio'][i] > best[0]):
                    best = (d['ratio'][i], s, i)
            elif (w is None or i > w + watch_bars) and watch_ok(d, i, vol, down):
                watch[s] = i
        if best:
            _, s, i = best
            entry = pre[s]['cs'][i][4] * (1 + SLIP)
            pos = {'sym': s, 'i': i, 'entry': entry, 'tp': target_price(entry, profit),
                   'stop': entry * (1 - stop) if stop else 0}
            watch = {}
    wins = [r for r in results if r > 0]
    losses = [r for r in results if r <= 0]
    return {
        'trades': len(results), 'wins': len(wins), 'losses': len(losses),
        'win_rate': round(100 * len(wins) / len(results), 1) if results else 0,
        'gross_win': round(sum(wins), 4), 'gross_loss': round(sum(losses), 4),
        'net': round(sum(results), 4),
        'avg_loss': round(sum(losses) / len(losses), 4) if losses else 0,
    }


def load_data(ex):
    coins = pick_coins(ex)
    log(f"العملات: {', '.join(coins)}")
    data = {}
    for s in coins:
        try:
            data[s] = fetch_history(ex, s, BACKTEST_DAYS)
            log(f"  {s}: {len(data[s])} شمعة")
        except Exception as e:
            log(f"  تخطيت {s}: {e}")
    return data


def backtest():
    log("🤖 بدأ اختبار استراتيجية الزخم وكسر الهبوط...", notify=True)
    data = load_data(Binance())
    if not data:
        log("⚠️ لم أستطع تحميل أي بيانات. أعد التشغيل.", notify=True)
        return
    pre, timeline = build(data)
    r = simulate(pre, timeline)
    text = "\n".join([
        "========== النتيجة ==========",
        f"آخر {BACKTEST_DAYS} يوم | المبلغ {TRADE_USDT}$ | هدف +{NET_PROFIT*100:g}% | وقف -{STOP*100:g}% | أقصى مدة {MAX_MINUTES} دقيقة",
        f"عدد الصفقات: {r['trades']}  (رابحة {r['wins']} / خاسرة {r['losses']})",
        f"نسبة النجاح: {r['win_rate']}%",
        f"مجموع الأرباح: +{r['gross_win']}$ | مجموع الخسائر: {r['gross_loss']}$",
        f"💰 الصافي بعد الرسوم: {r['net']:+}$  (من {TRADE_USDT}$)",
    ])
    print("\n" + text)
    tg.send("📋 " + text)


def sweep():
    log("🤖 بدأ اختبار عدة إعدادات على نفس البيانات...", notify=True)
    data = load_data(Binance())
    if not data:
        log("⚠️ لم أستطع تحميل أي بيانات. أعد التشغيل.", notify=True)
        return
    pre, timeline = build(data)
    mid = timeline[len(timeline) // 2][0]
    first = [x for x in timeline if x[0] < mid]
    second = [x for x in timeline if x[0] >= mid]

    grid = list(itertools.product((2, 3, 5), (1, 2, 3), (1, 1.5, 2, 3), (2, 4, 0), (30, 60, 120)))
    rows = []
    for v, d, p, st, m in grid:
        kw = dict(vol=v, down=d / 100, profit=p / 100, stop=st / 100, maxm=m)
        rows.append((v, d, p, st, m, simulate(pre, first, **kw), simulate(pre, second, **kw)))
    log(f"اختبرت {len(rows)} إعداداً")

    ok = sorted([r for r in rows if r[5]['trades'] >= 5], key=lambda r: r[5]['net'], reverse=True)
    lines = ["========== النتيجة ==========",
             f"{BACKTEST_DAYS} يوم: النصف الأول للاختيار، والثاني للتحقق (لم يُستخدم في الاختيار)",
             "الإعدادات: حجم× / هبوط% / هدف% / وقف% / دقائق", ""]
    for v, d, p, st, m, a, b in ok[:5]:
        lines.append(f"{v:g}× / {d:g}% / {p:g}% / {st:g}% / {m}د")
        lines.append(f"   اختيار: {a['trades']} صفقة، {a['net']:+}$")
        lines.append(f"   تحقق: {b['trades']} صفقة، {b['net']:+}$")
    pos_all = sum(1 for r in rows if r[6]['net'] > 0)
    both = sum(1 for r in rows if r[5]['net'] > 0 and r[6]['net'] > 0) / len(rows)
    lines += ["", f"إعدادات رابحة في نصف التحقق: {pos_all} من {len(rows)}"]
    MIN_GAIN = round(TRADE_USDT * 0.02, 3)
    good = [r for r in ok[:5] if r[6]['net'] >= MIN_GAIN and r[6]['trades'] >= 10]
    lines.append(f"(شرط الصمود: ربح ≥ {MIN_GAIN}$ في التحقق مع 10 صفقات، وأن يربح 15% من الإعدادات في النصفين معاً. الآن: {both*100:.0f}%)")
    if good and both >= 0.15:
        v, d, p, st, m, a, b = good[0]
        lines.append(f"✅ أفضل إعداد صمد في التحقق: {v:g}× / {d:g}% / {p:g}% / {st:g}% / {m}د ({b['net']:+}$)")
        lines.append("تنبيه: عينة صغيرة، وهذا لا يضمن الربح مستقبلاً. جرّبه إشارات فقط أسبوعاً قبل أي مال.")
    else:
        lines.append("❌ لا يوجد إعداد صمد في نصف التحقق. لا توجد أفضلية موثوقة.")
    text = "\n".join(lines)
    print("\n" + text)
    tg.send("📋 " + text)


def walkforward():
    """هل اختيار العملات بحسب سجلّها يتنبأ فعلاً؟ نختار على RANK_DAYS يوماً، ثم نقيس على TEST_DAYS يوماً بعدها.
    عملة عملة لتوفير ذاكرة الهاتف."""
    log(f"🤖 بدأ اختبار الاختيار: أختار على {RANK_DAYS} يوماً وأتحقق على {TEST_DAYS} يوماً بعدها...", notify=True)
    ex = Binance()
    coins = pick_coins(ex)
    cutoff = int(time.time() * 1000) - int(TEST_DAYS) * 86400_000
    rows = []
    for n, s in enumerate(coins, 1):
        try:
            cs = fetch_history(ex, s, int(RANK_DAYS + TEST_DAYS))
        except Exception as e:
            log(f"  تخطيت {s}: {e}")
            continue
        before = [c for c in cs if c[0] < cutoff]
        if len(before) < 300 or len(cs) - len(before) < 300:
            continue
        pre_a, tl_a = build({s: before})
        a = simulate(pre_a, tl_a)                                  # سجلّها في فترة الاختيار
        pre_b, tl_b = build({s: cs})
        b = simulate(pre_b, [x for x in tl_b if x[0] >= cutoff])   # أداؤها بعدها (لم يُستخدم في الاختيار)
        rows.append((s, a, b))
        if n % 20 == 0:
            log(f"  {n}/{len(coins)} عملة...")

    def pick(min_samples):
        q = [r for r in rows if r[1]['trades'] >= min_samples and r[1]['win_rate'] >= MIN_WINRATE and r[1]['net'] > 0]
        return sorted(q, key=lambda r: (r[1]['win_rate'], r[1]['net']), reverse=True)[:int(TOP_FINAL)]

    def agg(rs):
        t = sum(r[2]['trades'] for r in rs)
        w = sum(r[2]['wins'] for r in rs)
        net = sum(r[2]['net'] for r in rs)
        return t, w, net

    def line(name, rs):
        t, w, net = agg(rs)
        if not t:
            return f"{name}: {len(rs)} عملة، لا صفقات في فترة التحقق"
        return (f"{name}: {len(rs)} عملة | {t} صفقة | نجاح {100*w/t:.0f}% | "
                f"الصافي {net:+.3f}$ | متوسط الصفقة {100*net/(t*TRADE_USDT):+.2f}%")

    cur, strict = pick(int(MIN_SAMPLES)), pick(10)
    t_cur, _, net_cur = agg(cur)
    t_all, _, net_all = agg(rows)
    lines = ["========== نتيجة اختبار الاختيار ==========",
             f"اخترت على {RANK_DAYS} يوماً، وقست على آخر {TEST_DAYS} يوماً (لم يرها الاختيار)",
             f"شامل الرسوم {FEE*200:g}% وانزلاق {SLIP*100:g}% لكل عملية | هدف +{NET_PROFIT*100:g}% | وقف -{STOP*100:g}%", "",
             line("كل العملات", rows),
             line(f"المختارة بإعدادك الحالي (≥{MIN_SAMPLES:g} إشارات)", cur),
             line("المختارة بشرط أقوى (≥10 إشارات)", strict), ""]
    avg_cur = net_cur / t_cur if t_cur else 0
    avg_all = net_all / t_all if t_all else 0
    if t_cur >= 20 and net_cur > 0 and avg_cur > avg_all:
        lines.append("✅ الاختيار صمد في فترة لم يرها: توجد أفضلية مبدئية بعد الرسوم. تابع الإشارات أسبوعاً وسجّل التنفيذ الفعلي قبل أي زيادة.")
    else:
        lines.append("❌ الاختيار بحسب السجلّ لم يصمد بعد الرسوم (أو الصفقات أقل من 20). لا توجد أفضلية موثوقة؛ لا تدخل بمال على هذه الإشارات.")
    text = "\n".join(lines)
    print("\n" + text)
    tg.send("📋 " + text)


def rank_coins(ex, coins):
    """يقيس لكل عملة: لو طُبّقت الاستراتيجية عليها في آخر RANK_DAYS يوماً، كم مرة وصلت للهدف قبل الوقف؟
    يرجع فقط العملات المؤهلة: {العملة: {'trades','wins','rate'}}"""
    out = {}
    for s in coins:
        try:
            cs = fetch_history(ex, s, RANK_DAYS)
            pre, timeline = build({s: cs})
            r = simulate(pre, timeline)
        except Exception as e:
            log(f"  تخطيت {s} في الترتيب: {e}")
            continue
        ok = r['trades'] >= MIN_SAMPLES and r['win_rate'] >= MIN_WINRATE and r['net'] > 0
        log(f"  {s}: {r['wins']}/{r['trades']} نجاح ({r['win_rate']}%) {'✔ مؤهلة' if ok else '✖'}")
        if ok:
            out[s] = {'trades': r['trades'], 'wins': r['wins'], 'rate': r['win_rate'], 'net': r['net']}
    best = sorted(out.items(), key=lambda kv: (kv[1]['rate'], kv[1]['net']), reverse=True)[:int(TOP_FINAL)]
    return dict(best)


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
    for s in all_coins:
        if ch.get(s, 0) < MIN_24H:
            continue                                    # الزخم: لم تربح 5% في 24 ساعة
        try:
            d = ex.candles(s, limit=40, interval='1d')[:-1]     # الشموع اليومية المغلقة
        except Exception:
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
    return all_coins, [s for _, _, s in good[:int(TOP_FINAL)]]


def run_signals():
    ex = Binance()
    print("🔎 أختار العملات...", flush=True)
    all_coins, coins = good_coins(ex)
    st = {'balance': TRADE_USDT, 'start': TRADE_USDT, 'trades': 0, 'wins': 0, 'pos': None, 'hist': []}
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE) as f:
                st.update(json.load(f))
        except Exception:
            pass
    watching = {}     # العملة -> {'high','low','until'}

    def save():
        with open(STATE_FILE, 'w') as f:
            json.dump(st, f, indent=2)

    def status():
        txt = (f"📊 الحالة\n"
               f"المبلغ الآن: {st['balance']:.3f}$\n"
               f"المكسب الكلي: {st['balance'] - st['start']:+.3f}$\n"
               f"الصفقات: {st['trades']} (رابحة {st['wins']})\n")
        p = st['pos']
        if p:
            try:
                now = ex.price(p['sym'])
                val = net_value(p['amount'], p['entry'], now)
                left = MAX_MINUTES - (time.time() - p['t']) / 60
                txt += (f"\n🟡 صفقة مفتوحة: {p['sym']}\n"
                        f"الدخول {fmt_p(p['entry'])} | الآن {fmt_p(now)}\n"
                        f"قيمتها الآن: {val:.3f}$ ({val - p['amount']:+.3f}$)\n"
                        f"الهدف {fmt_p(p['tp'])} | الوقف {fmt_p(p['stop']) if p['stop'] else 'بدون'} | باقي {max(0, left):.0f} دقيقة")
            except Exception:
                txt += f"\nصفقة مفتوحة: {p['sym']}"
        elif watching:
            txt += "\n👀 أراقب الآن: " + "، ".join(watching)
        else:
            txt += "\nأبحث عن مطرقة..."
        return txt

    tg.start_commands(status, on_log=lambda: jr.send_all(tg))
    mkt0 = market_state(ex)
    t_start = time.time()
    tg.send(rtl([
        "🚀 البوت يعمل", LINE,
        "💎 الرصيد: " + N("%.2f$" % st['balance']), LINE,
        "🎯 شروط الدخول",
        f"• الصعود 24س: {N(f'≥{MIN_24H:g}%')}",
        "• الارتداد: مطرقة 15د + تأكيد الساعة + اليومي جيد",
        f"• RSI6: 15د {N(f'≤{RSI_15M:g}')} · ساعة {N(f'<{RSI_1H:g}')} · يومي {N(f'<{RSI_1D:g}')}",
        f"• الاختراق: كسر قمة المطرقة بحجم {N(f'≥{BRK_VOL:g}×')}",
        f"• حجم المطرقة: {N(f'≥{HAMMER_VOL:g}')} أضعاف المعتاد",
        f"• فلتر السوق: لا دخول عند 🔻 هابط بقوة",
        f"• Spread: {N(f'≤{MAX_SPREAD*100:g}%')}", LINE,
        "🛡️ الحماية",
        f"• الهدف: {N(f'+{NET_PROFIT*100:g}%')} صافي بعد الرسوم",
        f"• وقف الخسارة: تحت قاع المطرقة، حتى {N(f'{STOP*100:g}%')}",
        f"• أقصى مدة: {N(int(MAX_MINUTES))} دقيقة", LINE,
        "📐 حجم الصفقة: " + N("%.2f$" % st['balance']),
        "🧱 الوقف: يدوي (OCO)",
        f"🔎 العملات المقبولة الآن: {N(len(coins))} من {N(len(all_coins))}",
        f"🌍 مزاج السوق: {mkt0}",
        f"📊 تقرير يومي: {N('12:00')}",
        f"🗓️ تقرير أسبوعي: السبت {N('09:00')}",
        "✅ جاهز"]))
    jr.event('بدء', '', الرصيد=st['balance'], السوق=mkt0, المقبولة=len(coins), الكل=len(all_coins),
             العملات=' '.join(coins), RSI_15M=RSI_15M, RSI_1H=RSI_1H, RSI_1D=RSI_1D, HAMMER_VOL=HAMMER_VOL,
             BREAK_VOL=BRK_VOL, MIN_24H=MIN_24H, MAX_SPREAD=MAX_SPREAD * 100, هدف=NET_PROFIT * 100,
             وقف=STOP * 100, أقصى_مدة=MAX_MINUTES)
    last_refresh, last_err, last_hb, last_bar = time.time(), 0, time.time(), 0
    last_daily, last_weekly, last_scan = None, None, ''
    mkt = mkt0

    def stats(since):
        h = [g for t, g in st['hist'] if t >= since]
        return len(h), sum(1 for g in h if g > 0), sum(h)

    def day_pct():
        midnight = datetime.now(TZ).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        _, _, g = stats(midnight)
        base = st['balance'] - g
        return 100 * g / base if base > 0 else 0

    def report(title, since, label):
        n, w, g = stats(since)
        base = st['balance'] - g
        return rtl([
            title, LINE,
            "💰 الرصيد: " + N("%.2f$" % st['balance']),
            f"📊 أرباح {label}: {N(f'{100 * g / base if base > 0 else 0:+.2f}%')} ({N(f'{g:+.3f}$')})",
            f"🧾 الصفقات: {N(n)} · ✅ رابحة: {N(w)} · 🩸 خاسرة: {N(n - w)}",
            f"🏆 نسبة النجاح: {N(f'{100 * w / n:.1f}%' if n else '—')}", LINE,
            f"📈 منذ البداية: {N(st['trades'])} صفقة · " + N("%+.3f$" % (st['balance'] - st['start']))])
    after = []        # متابعة السعر بعد الخروج: {'no','sym','price','t','d30','d60'}

    def ch24_now(s):
        return float(ex._get('/api/v3/ticker/24hr', {'symbol': s})['priceChangePercent'])

    while True:
        try:
            if time.time() - last_refresh > 3600 and not st['pos']:
                all_coins, coins = good_coins(ex)
                last_refresh = time.time()
                log(f"حدّثت العملات المقبولة: {len(coins)} عملة")
                jr.event('تحديث_العملات', '', المقبولة=len(coins), الكل=len(all_coins), العملات=' '.join(coins))

            # متابعة ما حدث للسعر بعد الخروج (هل خرجنا مبكراً أم في الوقت الصحيح؟)
            for a_ in list(after):
                age = (time.time() - a_['t']) / 60
                for mins, key, col in ((30, 'd30', 'السعر_بعد30د_%'), (60, 'd60', 'السعر_بعد60د_%')):
                    if not a_[key] and age >= mins:
                        pc = 100 * (ex.price(a_['sym']) / a_['price'] - 1)
                        jr.update_after(a_['no'], col, pc)
                        jr.event('بعد_الخروج', a_['sym'], دقائق=mins, التغير_بعد_الخروج=pc)
                        a_[key] = True
                if a_['d30'] and a_['d60']:
                    after.remove(a_)

            p = st['pos']
            if p:
                now = ex.price(p['sym'])
                p['hi'] = max(p.get('hi', p['entry']), now)
                p['lo'] = min(p.get('lo', p['entry']), now)
                minutes = (time.time() - p['t']) / 60
                hit = now >= p['tp']
                stopped = bool(p['stop']) and now <= p['stop']
                if hit or stopped or minutes >= MAX_MINUTES:
                    out = p['amount'] * (1 + NET_PROFIT) if hit else net_value(p['amount'], p['entry'], now)
                    gain = out - p['amount']
                    st['balance'] = out
                    st['trades'] += 1
                    st['wins'] += 1 if gain > 0 else 0
                    st.setdefault('hist', []).append([time.time(), gain])
                    st['hist'] = [x for x in st['hist'] if x[0] > time.time() - 35 * 86400]
                    st['pos'] = None
                    save()
                    why = ("🎯 وصل الهدف" if hit else
                           "🛑 وقف الخسارة (كسر قاع المطرقة)" if stopped else
                           f"⏱ مرّت {MAX_MINUTES} دقيقة، اخرج وانتقل لغيرها")
                    tg.send(f"🔴 اخرج: {p['sym']}\nالمبلغ: {out:.2f}$\n{'الربح' if gain > 0 else 'الخسارة'}: {gain:+.2f}$")
                    log(f"اخرج {p['sym']} | {why} | {gain:+.3f}$")
                    m = p.get('m', {})
                    jr.trade({
                        'رقم': st['trades'], 'العملة': p['sym'],
                        'وقت_المراقبة': m.get('watch_time', ''), 'وقت_الدخول': m.get('entry_time', ''),
                        'وقت_الخروج': jr.now_str(), 'انتظار_للدخول_د': m.get('wait', ''), 'مدة_الصفقة_د': minutes,
                        'سعر_الدخول': p['entry'], 'سعر_الخروج': now, 'الهدف': p['tp'], 'الوقف': p['stop'],
                        'المبلغ_داخل': p['amount'], 'المبلغ_خارج': out, 'الربح_$': gain,
                        'الربح_%': 100 * gain / p['amount'], 'النتيجة': 'ربح' if gain > 0 else 'خسارة',
                        'سبب_الخروج': 'الهدف' if hit else 'الوقف' if stopped else 'انتهاء_المدة',
                        'أعلى_سعر_%': 100 * (p['hi'] / p['entry'] - 1), 'أدنى_سعر_%': 100 * (p['lo'] / p['entry'] - 1),
                        'ربح24س_عند_المراقبة': m.get('ch24'), 'ربح24س_عند_الدخول': m.get('ch24_entry'),
                        'حجم_المطرقة×': m.get('vr'), 'RSI6_15د': m.get('r15'), 'RSI6_ساعة': m.get('r1h'),
                        'RSI6_يومي': m.get('r1d'), 'مطرقة_ساعة': m.get('h1_hammer'), 'مطرقة_يومي': m.get('d_hammer'),
                        'فوق_متوسط20_يومي': m.get('d_above'), 'القوة_نجوم': m.get('stars'),
                        'السوق_عند_المراقبة': m.get('mkt_watch'), 'السوق_عند_الدخول': m.get('mkt_entry'),
                        'Spread_%': m.get('spread'), 'حجم_الكسر×': m.get('brk_vr'),
                        'قمة_المطرقة': m.get('high'), 'قاع_المطرقة': m.get('low'),
                    })
                    tg.send(f"📋 تفاصيل الصفقة {st['trades']}: {p['sym']}\n"
                            f"النتيجة: {'ربح' if gain > 0 else 'خسارة'} {gain:+.3f}$ ({100 * gain / p['amount']:+.2f}%)\n"
                            f"سبب الخروج: {'الهدف' if hit else 'الوقف' if stopped else 'انتهاء المدة'}\n"
                            f"الدخول {fmt_p(p['entry'])} ← الخروج {fmt_p(now)}\n"
                            f"المدة: {minutes:.0f} د | انتظار قبل الدخول: {m.get('wait', 0):.0f} د\n"
                            f"أعلى سعر: {100 * (p['hi'] / p['entry'] - 1):+.2f}% | أدنى سعر: {100 * (p['lo'] / p['entry'] - 1):+.2f}%\n"
                            f"ربح 24س: {m.get('ch24') or 0:+.1f}% عند المراقبة، {m.get('ch24_entry') or 0:+.1f}% عند الدخول\n"
                            f"RSI6: ‏15د {m.get('r15') or 0:.0f} | ساعة {m.get('r1h') or 0:.0f} | يومي {m.get('r1d') or 0:.0f}\n"
                            f"حجم المطرقة ×{m.get('vr') or 0:.1f} | حجم الكسر ×{m.get('brk_vr') or 0:.1f} | Spread {m.get('spread') or 0:.2f}%\n"
                            f"مطرقة الساعة: {'نعم' if m.get('h1_hammer') else 'لا'} | مطرقة اليومي: {'نعم' if m.get('d_hammer') else 'لا'}\n"
                            f"القوة: {'⭐' * int(m.get('stars') or 0)} | السوق: {m.get('mkt_entry', '')}\n"
                            f"الرصيد: {st['balance']:.3f}$ | الصفقات {st['trades']} (رابحة {st['wins']})")
                    jr.event('خروج', p['sym'], السبب='الهدف' if hit else 'الوقف' if stopped else 'انتهاء_المدة',
                             السعر=now, الربح=gain, الرصيد=st['balance'], مدة=minutes,
                             أعلى=100 * (p['hi'] / p['entry'] - 1), أدنى=100 * (p['lo'] / p['entry'] - 1))
                    after.append({'no': st['trades'], 'sym': p['sym'], 'price': now, 't': time.time(),
                                  'd30': False, 'd60': False})
                else:
                    if not p.get('prep'):
                        progress = (now - p['entry']) / (p['tp'] - p['entry'])
                        if progress >= 0.75 or MAX_MINUTES - minutes <= 5:
                            p['prep'] = True
                            log(f"استعد للخروج: {p['sym']}")
                    save()
            else:
                # 1) بعد إغلاق كل شمعة 15 دقيقة: ابحث عن مطارق جديدة
                bar = int(time.time() // 900)
                if bar != last_bar and time.time() % 900 >= 5:
                    last_bar = bar
                    mkt = market_state(ex)
                    found = 0
                    if 'هابط بقوة' in mkt:
                        log("السوق هابط بقوة: لا أبحث عن دخول الآن")
                    for s in ([] if 'هابط بقوة' in mkt else coins):
                        if s in watching or len(watching) >= MAX_WATCH:
                            continue
                        last_scan = s
                        cs = ex.candles(s, limit=60, interval='15m')[:-1]
                        if len(cs) < 30 or not is_hammer(cs, len(cs) - 1):
                            continue
                        found += 1
                        dd = getattr(ex, 'daily', {}).get(s, {})
                        r15 = rsi([x[4] for x in cs])
                        r15m = min(r15[-3:])
                        avg_v = sum(x[5] for x in cs[-21:-1]) / 20
                        vr = cs[-1][5] / avg_v if avg_v > 0 else 0
                        vals = {'ربح24س': dd.get('ch'), 'حجم_المطرقة×': vr, 'RSI6_15د': r15m,
                                'RSI6_يومي': dd.get('rsi'), 'السوق': mkt, 'السعر': cs[-1][4]}
                        if r15m > RSI_15M:
                            jr.reject(s, f'RSI6_15د>{RSI_15M:g}', 'مطرقة', **vals)
                            continue                          # لا تشبّع بيعي: المطرقة ضعيفة
                        if vr < HAMMER_VOL:
                            jr.reject(s, f'حجم_المطرقة<{HAMMER_VOL:g}', 'مطرقة', **vals)
                            continue                          # المطرقة بلا حجم تداول
                        h1 = ex.candles(s, limit=30, interval='1h')[:-1]
                        last_h = h1[-1]
                        r1h = rsi([x[4] for x in h1])[-1]
                        h1_hammer = is_hammer(h1, len(h1) - 1)
                        vals['RSI6_ساعة'] = r1h
                        if not (h1_hammer or last_h[4] > last_h[1]):
                            jr.reject(s, 'الساعة_حمراء', 'مطرقة', **vals)
                            continue                          # الساعة لا تؤكد
                        if r1h >= RSI_1H:
                            jr.reject(s, f'RSI6_ساعة≥{RSI_1H:g}', 'مطرقة', **vals)
                            continue
                        sp = spread(ex, s)
                        vals['Spread_%'] = sp * 100
                        if sp > MAX_SPREAD:
                            jr.reject(s, 'Spread_كبير', 'مطرقة', **vals)
                            continue                          # فرق سعر كبير يأكل الربح
                        hm = cs[-1]
                        stars, label = coin_strength(dd.get('ch', 0), vr, r15m, h1_hammer, dd)
                        watching[s] = {'high': hm[2], 'low': hm[3], 'until': time.time() + 30 * 60,
                                       'm': {'watch_time': jr.now_str(), 'wt': time.time(), 'ch24': dd.get('ch'),
                                             'vr': vr, 'r15': r15m, 'r1h': r1h, 'r1d': dd.get('rsi'),
                                             'h1_hammer': h1_hammer, 'd_hammer': dd.get('hammer'),
                                             'd_above': dd.get('above'), 'stars': len(stars), 'mkt_watch': mkt,
                                             'high': hm[2], 'low': hm[3]}}
                        tg.send(f"👀 راقب: {s}\n"
                                f"المبلغ: {st['balance']:.2f}$\n"
                                f"القوة: {stars} {label}\n"
                                f"24 ساعة: {dd.get('ch', 0):+.1f}%")
                        log(f"راقب {s} | 24س {dd.get('ch', 0):+.1f}% | حجم ×{vr:.1f} | RSI6 15د {r15m:.0f} ساعة {r1h:.0f} يومي {dd.get('rsi', 0):.0f}")
                        jr.event('مراقبة', s, القوة=len(stars), **vals)
                    jr.event('فحص_15د', '', السوق=mkt, عملات=len(coins), مطارق=found, أراقب=' '.join(watching))
                # 2) كل 10 ثوانٍ: هل كسرت إحدى العملات قمة المطرقة؟
                for s, w in list(watching.items()):
                    now = ex.price(s)
                    m = w['m']
                    if time.time() > w['until']:
                        del watching[s]
                        log(f"ألغيت مراقبة {s}")
                        jr.reject(s, 'لم_تكسر_خلال_30د' + ('_كسر_ضعيف' if w.get('weak') else ''), 'مراقبة',
                                  **{'ربح24س': m['ch24'], 'حجم_المطرقة×': m['vr'], 'RSI6_15د': m['r15'],
                                     'RSI6_ساعة': m['r1h'], 'RSI6_يومي': m['r1d'], 'السوق': mkt, 'السعر': now,
                                     'حجم_الكسر×': w.get('weak')})
                    elif now > w['high'] and now <= w['high'] * (1 + MAX_CHASE + 0.002):
                        # قياس الكسر: هل الكسر بحجم تداول حقيقي؟
                        k = ex.candles(s, limit=21, interval='15m')
                        avg_v = sum(x[5] for x in k[:-1]) / max(1, len(k) - 1)
                        frac = max(0.2, (time.time() * 1000 - k[-1][0]) / 900_000)
                        brk_vr = (k[-1][5] / frac) / avg_v if avg_v > 0 else 0
                        if brk_vr < BRK_VOL:
                            if not w.get('weak'):
                                jr.event('كسر_ضعيف', s, حجم_الكسر=brk_vr, السعر=now)
                            w['weak'] = brk_vr
                            continue                      # كسر ضعيف بلا حجم: ننتظر
                        c24 = ch24_now(s)
                        if c24 < MIN_24H:
                            del watching[s]
                            log(f"تخطيت {s}: ربح 24 ساعة نزل تحت {MIN_24H:g}%")
                            jr.reject(s, f'ربح24س<{MIN_24H:g}', 'دخول', **{'ربح24س': c24, 'السعر': now,
                                      'حجم_الكسر×': brk_vr, 'السوق': mkt})
                            continue                      # الشرط الأساسي: رابحة ≥5% في 24 ساعة لحظة الدخول
                        sp = spread(ex, s)
                        if sp > MAX_SPREAD:
                            if not w.get('wide'):
                                jr.event('Spread_اتسع', s, Spread=sp * 100)
                            w['wide'] = True
                            continue                      # فرق السعر اتسع: ننتظر
                        tp = ex.ceil_price(s, target_price(now))
                        stop = max(w['low'] * 0.998, now * (1 - STOP)) if STOP else w['low'] * 0.998
                        m.update({'entry_time': jr.now_str(), 'wait': (time.time() - m['wt']) / 60,
                                  'ch24_entry': c24, 'mkt_entry': mkt, 'spread': sp * 100, 'brk_vr': brk_vr})
                        st['pos'] = {'sym': s, 'entry': now, 'tp': tp, 'stop': stop,
                                     'amount': st['balance'], 't': time.time(), 'hi': now, 'lo': now, 'm': m}
                        watching.clear()
                        save()
                        tg.send(f"🟢 ادخل: {s}\n"
                                f"المبلغ: {st['balance']:.2f}$\n"
                                f"🎯 الهدف: {fmt_p(tp)}\n"
                                f"🛑 الوقف: {fmt_p(stop)}")
                        log(f"ادخل {s} | حجم الكسر ×{brk_vr:.1f} | بسعر {fmt_p(now)} | هدف {fmt_p(tp)} | وقف {fmt_p(stop)}")
                        jr.event('دخول', s, السعر=now, الهدف=tp, الوقف=stop, المبلغ=st['balance'],
                                 حجم_الكسر=brk_vr, ربح24س=c24, Spread=sp * 100, السوق=mkt)
                        break
                    elif now > w['high']:
                        del watching[s]
                        log(f"تخطيت {s}: السعر هرب فوق قمة المطرقة")
                        jr.reject(s, 'السعر_هرب_فوق_القمة', 'مراقبة',
                                  **{'ربح24س': m['ch24'], 'حجم_المطرقة×': m['vr'], 'RSI6_15د': m['r15'],
                                     'RSI6_ساعة': m['r1h'], 'RSI6_يومي': m['r1d'], 'السوق': mkt, 'السعر': now})
        except Exception as e:
            notify = time.time() - last_err > 1800
            if notify:
                last_err = time.time()
            log(f"⚠️ خطأ: {e}", notify=notify)
            jr.event('خطأ', '', الرسالة=str(e)[:200])
        nowl = datetime.now(TZ)
        if nowl.hour >= 12 and last_daily != nowl.date():
            if last_daily is not None or nowl.hour == 12:
                tg.send(report("📊 التقرير اليومي", time.time() - 86400, "آخر 24 ساعة"))
                jr.send_all(tg, '📂 السجل اليومي الكامل')
            last_daily = nowl.date()
        if nowl.weekday() == 5 and nowl.hour >= 9 and last_weekly != nowl.date():
            if last_weekly is not None or nowl.hour == 9:
                tg.send(report("🗓️ التقرير الأسبوعي", time.time() - 7 * 86400, "الأسبوع"))
            last_weekly = nowl.date()
        if HEARTBEAT_MIN and time.time() - last_hb >= HEARTBEAT_MIN * 60:
            last_hb = time.time()
            pos = st['pos']
            mkt_hb = market_state(ex)
            up = int(time.time() - t_start) // 60
            n_all, w_all = st['trades'], st['wins']
            tg.send(rtl([
                "💓 تقرير", LINE,
                "💰 الرصيد: " + N("%.2f$" % st['balance']),
                f"🌍 مزاج السوق: {mkt_hb}",
                f"📊 أرباح اليوم: {N(f'{day_pct():+.2f}%')}",
                f"🔍 آخر فحص: {N(last_scan[:-4] + '-USDT' if last_scan else '—')}", LINE,
                f"🏆 نسبة النجاح: {N(f'{100 * w_all / n_all:.1f}%' if n_all else '—')}",
                f"✅ رابحة: {N(w_all)} · 🩸 خاسرة: {N(n_all - w_all)}",
                f"📂 صفقات مفتوحة: {N(1 if pos else 0)}" + (f" ({N(pos['sym'])})" if pos else ""),
                ] + ([f"👀 أراقب: {N(' '.join(watching))}"] if watching else []) + [LINE,
                f"🟢 يعمل · التشغيل: {N(up // 60)}س {N(up % 60)}د"]))
            jr.event('البوت_يعمل', '', الرصيد=st['balance'], السوق=mkt_hb,
                     صفقة=pos['sym'] if pos else '', أراقب=' '.join(watching))
        time.sleep(SCAN_EVERY)


if __name__ == '__main__':
    mode = sys.argv[1] if len(sys.argv) > 1 else os.environ.get('MODE', 'signals').strip().lower()
    if mode == 'backtest':
        backtest()
    elif mode == 'sweep':
        sweep()
    elif mode == 'walkforward':
        walkforward()
    else:
        run_signals()
