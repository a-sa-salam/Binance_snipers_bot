"""
بوت ترشيح - Binance Spot (إشارات فقط، لا يلمس حسابك)
-----------------------------------------------------
الاستراتيجية: المطارق اليابانية على 15 دقيقة + الساعة + اليوم
  0) الزخم: كل عملات المنصة، المقبولة فقط التي ربحت ≥5% في 24 ساعة، والأقوى أولاً، ونأخذ الجيدة (فوق متوسط 20 يوماً أو مطرقة يومية) حتى 50 عملة
  1) 👀 راقب:   مطرقة على 15 دقيقة بعد هبوط + RSI6 ≤ 35 + حجم ≥ ×1.2، والساعة تؤكد (مطرقة أو خضراء، RSI6 < 70)
     واليومي: RSI6 < 90
  2) 🟢 ادخل الآن: خلال 30 دقيقة يكسر السعر قمة المطرقة بحجم تداول قوي. الوقف تحت قاع المطرقة (حتى -2%)
  3) 🔴 اخرج الآن: عند الهدف +1.5% صافي، أو وقف الخسارة، أو انتهاء المدة. وقبلها: ⚠️ استعد للخروج

التشغيل: اضبط MODE في ملف .env
  MODE=signals   -> الإشارات المباشرة في تيليجرام
  MODE=backtest  -> اختبار الاستراتيجية على الأيام الماضية
  MODE=sweep     -> يجرّب عدة إعدادات ويتحقق منها على نصف لم يُستخدم في الاختيار
  MODE=walkforward -> يختبر "اختيار العملات بحسب سجلّها": يختار على 30 يوماً ثم يقيس على 14 يوماً بعدها لم يرها
الملفات:
  scanner.py      -> ماسح السوق (الإعدادات، Binance، المؤشرات، اختيار العملات، مزاج السوق)
  bot.py          -> البوت الرئيسي (المراقبة، الدخول، الخروج، الرسائل، التقارير) — شغّل هذا الملف
  telegram_bot.py -> تيليجرام | journal.py -> السجل
لا يحتاج أي مكتبات إضافية.
"""
import os, sys, json, time, math, itertools
from datetime import datetime
from scanner import *          # ماسح السوق: الإعدادات، Binance، المؤشرات، اختيار العملات
from scanner import _num
import telegram_bot as tg
import journal as jr


def log(msg, notify=False):
    t = datetime.now(TZ).strftime('%Y-%m-%d %H:%M:%S')
    print(f"[{t}] {msg}", flush=True)
    if notify:
        tg.send(f"{msg}\n🕒 {datetime.now(TZ).strftime('%H:%M:%S')} (الخرطوم)")



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
                # 1) كل 15 دقيقة: السكنر يفحص السوق ويرفع إشارات المراقبة
                bar = int(time.time() // 900)
                if bar != last_bar and time.time() % 900 >= 5:
                    last_bar = bar
                    mkt = market_state(ex)
                    if 'هابط بقوة' in mkt:
                        log("السوق هابط بقوة: لا أبحث عن دخول الآن")
                    sigs, rejects, last, found = scan_hammers(ex, coins, skip=watching,
                                                              slots=MAX_WATCH - len(watching), mkt=mkt)
                    last_scan = last or last_scan
                    for s, reason, vals in rejects:
                        jr.reject(s, reason, 'مطرقة', **vals)
                    for g in sigs:
                        s = g['sym']
                        watching[s] = {'high': g['high'], 'low': g['low'], 'until': time.time() + 30 * 60,
                                       'm': {'watch_time': jr.now_str(), 'wt': time.time(), 'ch24': g['ch24'],
                                             'vr': g['vr'], 'r15': g['r15'], 'r1h': g['r1h'], 'r1d': g['r1d'],
                                             'h1_hammer': g['h1_hammer'], 'd_hammer': g['d_hammer'],
                                             'd_above': g['d_above'], 'stars': len(g['stars']), 'mkt_watch': mkt,
                                             'high': g['high'], 'low': g['low']}}
                        tg.send(f"👀 راقب: {s}\n"
                                f"المبلغ: {st['balance']:.2f}$\n"
                                f"القوة: {g['stars']} {g['label']}\n"
                                f"24 ساعة: {(g['ch24'] or 0):+.1f}%")
                        log(f"راقب {s} | 24س {(g['ch24'] or 0):+.1f}% | حجم ×{g['vr']:.1f} | RSI6 15د {g['r15']:.0f} ساعة {g['r1h']:.0f} يومي {(g['r1d'] or 0):.0f}")
                        jr.event('مراقبة', s, القوة=len(g['stars']), **g['vals'])
                    jr.event('فحص_15د', '', السوق=mkt, عملات=len(coins), مطارق=found, أراقب=' '.join(watching))
                # 2) كل 10 ثوانٍ: السكنر يفحص الكسر، والبوت يقرر الدخول ويدير المخاطرة
                for s, w in list(watching.items()):
                    m = w['m']
                    r = check_entry(ex, s, w)
                    now = r['price']
                    if r['action'] == 'drop':
                        del watching[s]
                        log(f"ألغيت مراقبة {s}: {r['reason']}")
                        jr.reject(s, r['reason'], 'دخول' if 'ربح24س' in r['reason'] else 'مراقبة',
                                  **{'ربح24س': r.get('ch24', m['ch24']), 'حجم_المطرقة×': m['vr'], 'RSI6_15د': m['r15'],
                                     'RSI6_ساعة': m['r1h'], 'RSI6_يومي': m['r1d'], 'السوق': mkt, 'السعر': now,
                                     'حجم_الكسر×': r.get('brk_vr', w.get('weak'))})
                    elif r['action'] == 'wait':
                        key = 'weak' if r['reason'] == 'كسر_ضعيف' else 'wide'
                        if not w.get(key):
                            jr.event(r['reason'], s, حجم_الكسر=r.get('brk_vr'), Spread=r.get('spread', 0) * 100, السعر=now)
                        w[key] = r.get('brk_vr', True)
                    elif r['action'] == 'enter':
                        sp, brk_vr, c24 = r['spread'], r['brk_vr'], r['ch24']
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
