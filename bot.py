"""
================================================================
ربات سیگنال کریپتو - نسخه ۵.۱۵
================================================================
تغییرات این نسخه (نسبت به ۵.۱۴):
  ۱. رفع باگ Zone (ZONE_LOOKBACK=150، کانال از قبل پنجره شکست)
  ۲. حذف کندل در حال تشکیل (df.iloc[:-1])
  ۳. DIV_RECENCY=15 (واگرایی فقط تازه)
  ۴. ADX با DI+/DI- (جهت روند)
  ۵. Volume با جهت کندل
  ۶. if __name__ == "__main__"
  ۷. Telegram HTML + retry برای 429
  ۸. except Exception as e با لاگ
  ۹. fetch wrapper با ۳ retry
  ۱۰. تقسیم پیام به تکه‌های ≤ ۴۰۰۰ کاراکتر
  ۱۱. DOM با timestamp و مقایسه ۲۴ ساعت قبل
  ۱۲. حذف ثابت‌های بلااستفاده

================================================================
ویژگی‌های ذخیره‌شده برای آینده (کدش کامنت شده و غیرفعال است):
  - نسخه ۶.۰ (۱D تأیید)
  - نسخه ۶.۰ (RS vs BTC)
  - نسخه ۶.۰ (Funding Rate)
  - نسخه ۶.۰ (Open Interest)
  - نسخه ۶.۰ (حافظه‌دار کردن سیگنال‌ها)
================================================================
"""

import ccxt
import pandas as pd
import ta
import requests
import json
import os
import time
import html
from datetime import datetime, timezone

# ================================================================
# تنظیمات پایه
# ================================================================
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN', '')
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID', '')

# ================================================================
# اندیکاتورها
# ================================================================
DPO_LENGTH = 100
RSI_LENGTH = 14
RSI_MA_LENGTH = 14
MA_FAST = 50
MA_SLOW = 200
ADX_LENGTH = 14
DIVERGENCE_LOOKBACK = 100
BB_LENGTH = 20
BB_STD = 2
BB_SQUEEZE_THRESHOLD = 0.05

MA_CROSS_WINDOW = 5
ZONE_LOOKBACK = 150
ZONE_BREAK_WINDOW = 10
ZONE_BREAK_MARGIN_PCT = 0.2

DIV_RECENCY = 15

# ================================================================
# وزن‌ها
# ================================================================
WEIGHT_DPO_CROSS = 3
WEIGHT_DPO_DIR = 1
WEIGHT_RSI = 1
WEIGHT_DIV_REG = 2
WEIGHT_DIV_HID = 2
WEIGHT_TREND_RSI = 1
WEIGHT_MA50 = 1
WEIGHT_MA200 = 1
WEIGHT_ADX = 1
WEIGHT_VOLUME = 1
WEIGHT_BB = 1
WEIGHT_ZONE = 2
WEIGHT_1H = 2

# ================================================================
# آستانه‌ها
# ================================================================
THRESHOLD_WEAK = 5
THRESHOLD_SHOW = 6
THRESHOLD_MEDIUM = 8
THRESHOLD_STRONG = 11
THRESHOLD_VERY_STRONG = 15

# ================================================================
# فیلترها
# ================================================================
TOP_COINS_COUNT = 100
MIN_VOLUME_USDT = 1000000

DIV_MIN_DISTANCE = 5
DIV_RSI_DIFF = 3
DIV_RSI_OVERSOLD = 35
DIV_RSI_OVERBOUGHT = 65

ZONE_RED_PCT = 10
ZONE_BLUE_PCT = 25

DOM_COMPARE_HOURS = 24
DOM_HISTORY_MAX = 48

# ================================================================
# لیست سیاه
# ================================================================
BLACKLIST = [
    'USDC', 'USDT', 'USD1', 'DAI', 'FDUSD', 'TUSD', 'BUSD', 'USDD', 'USDP', 'GUSD', 'PYUSD',
    'WBTC', 'WETH', 'STETH', 'WSTETH', 'WBETH', 'RETH', 'CBETH', 'BETH', 'WBNB', 'WAVAX', 'WMATIC',
    'MSTRB', 'NVDAB', 'TSLAB', 'SPCXB', 'COINB', 'HOODB'
]

DOM_FILE = "dom_data.json"

# ================================================================
# شمارنده خطاها
# ================================================================
error_count = 0

def log_error(context, e):
    global error_count
    error_count += 1
    print(f"❌ خطا در {context}: {type(e).__name__}: {str(e)[:120]}")

# ================================================================
# ارسال پیام تلگرام (HTML + retry)
# ================================================================
def send_telegram_html(message):
    global error_count
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "parse_mode": "HTML",
            "disable_web_page_preview": True
        }
        for attempt in range(3):
            try:
                r = requests.post(url, json=payload, timeout=20)
                data = r.json()
                if data.get('ok'):
                    return True
                if data.get('error_code') == 429:
                    retry_after = data.get('parameters', {}).get('retry_after', 5)
                    print(f"⏳ Rate limit تلگرام، صبر {retry_after}s...")
                    time.sleep(retry_after)
                    continue
                print(f"خطا در تلگرام: {data}")
                return False
            except Exception as e:
                log_error("send_telegram attempt", e)
                time.sleep(2)
        return False
    except Exception as e:
        log_error("send_telegram", e)
        return False

def split_and_send(header, signals_list, is_buy=True):
    """تقسیم پیام روی مرز سیگنال‌ها و ارسال تکه‌تکه"""
    if not signals_list:
        return
    arrow = "⬆️" if is_buy else "⬇️"
    label = "خرید" if is_buy else "فروش"
    
    current_chunk = f"{header}\n─────────────\n\n{arrow} <b>{label}</b>\n"
    chunks = []
    
    for block in signals_list:
        if len(current_chunk) + len(block) > 3900:
            chunks.append(current_chunk)
            current_chunk = f"{header} (ادامه)\n─────────────\n\n{arrow} <b>{label}</b>\n"
        current_chunk += block
    chunks.append(current_chunk)
    
    for i, chunk in enumerate(chunks):
        ok = send_telegram_html(chunk)
        if ok:
            print(f"✅ پیام {label} تکه {i+1}/{len(chunks)} ارسال شد")
        else:
            print(f"❌ خطا در ارسال تکه {i+1}")

# ================================================================
# JSON
# ================================================================
def load_json(filename):
    if os.path.exists(filename):
        try:
            with open(filename, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            log_error(f"load_json {filename}", e)
            return {}
    return {}

def save_json(filename, data):
    try:
        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        log_error(f"save_json {filename}", e)

def load_dom_history():
    data = load_json(DOM_FILE)
    if isinstance(data, list):
        return data
    return []

def save_dom_history(history):
    history = history[-DOM_HISTORY_MAX:]
    save_json(DOM_FILE, history)

def find_dom_24h_ago(history):
    """پیدا کردن snapshot نزدیک به ۲۴ ساعت قبل"""
    if not history:
        return None
    now = time.time()
    target = now - DOM_COMPARE_HOURS * 3600
    best = None
    best_diff = float('inf')
    for entry in history:
        ts = entry.get('timestamp', 0)
        diff = abs(ts - target)
        if diff < best_diff and diff < 6 * 3600:
            best_diff = diff
            best = entry
    return best

# ================================================================
# fetch_ohlcv با retry
# ================================================================
def fetch_ohlcv_safe(exchange, symbol, timeframe='4h', limit=300, retries=3):
    for attempt in range(retries):
        try:
            data = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
            if data and len(data) > 0:
                return data
        except Exception as e:
            if attempt == retries - 1:
                log_error(f"fetch_ohlcv {symbol} {timeframe}", e)
            time.sleep(1)
    return None

# ================================================================
# DPO
# ================================================================
def calculate_dpo(close, period):
    sma_period = period // 2 + 1
    sma = close.rolling(window=sma_period).mean()
    dpo = close - sma
    return dpo.shift(period // 2 + 1)

# ================================================================
# محاسبه اندیکاتورها (روی کندل‌های بسته‌شده)
# ================================================================
def calculate_indicators(df):
    df['dpo'] = calculate_dpo(df['close'], DPO_LENGTH)
    df['rsi'] = ta.momentum.RSIIndicator(close=df['close'], window=RSI_LENGTH).rsi()
    df['rsi_ma'] = df['rsi'].rolling(window=RSI_MA_LENGTH).mean()
    df['ma_fast'] = df['close'].rolling(window=MA_FAST).mean()
    df['ma_slow'] = df['close'].rolling(window=MA_SLOW).mean()
    adx_ind = ta.trend.ADXIndicator(high=df['high'], low=df['low'], close=df['close'], window=ADX_LENGTH)
    df['adx'] = adx_ind.adx()
    df['di_plus'] = adx_ind.adx_pos()
    df['di_minus'] = adx_ind.adx_neg()
    bb = ta.volatility.BollingerBands(close=df['close'], window=BB_LENGTH, window_dev=BB_STD)
    df['bb_high'] = bb.bollinger_hband()
    df['bb_low'] = bb.bollinger_lband()
    df['bb_mid'] = bb.bollinger_mavg()
    df['bb_width'] = (df['bb_high'] - df['bb_low']) / df['bb_mid']
    df['vol_ma'] = df['volume'].rolling(window=20).mean()
    return df

# ================================================================
# DPO کراس
# ================================================================
def check_dpo_buy(df):
    recent_dpo = df['dpo'].tail(2).reset_index(drop=True)
    crossed_up = False
    if len(recent_dpo) >= 2:
        if recent_dpo.iloc[0] < 0 and recent_dpo.iloc[-1] > 0:
            crossed_up = True
    last_dpo = df['dpo'].iloc[-1]
    if last_dpo > 0:
        if crossed_up:
            return WEIGHT_DPO_CROSS, True
        else:
            return WEIGHT_DPO_DIR, False
    return 0, False

def check_dpo_sell(df):
    recent_dpo = df['dpo'].tail(2).reset_index(drop=True)
    crossed_down = False
    if len(recent_dpo) >= 2:
        if recent_dpo.iloc[0] > 0 and recent_dpo.iloc[-1] < 0:
            crossed_down = True
    last_dpo = df['dpo'].iloc[-1]
    if last_dpo < 0:
        if crossed_down:
            return WEIGHT_DPO_CROSS, True
        else:
            return WEIGHT_DPO_DIR, False
    return 0, False

# ================================================================
# MA کراس تازه
# ================================================================
def check_ma_buy(df, ma_col):
    recent = df.tail(MA_CROSS_WINDOW + 1).reset_index(drop=True)
    if len(recent) < 2:
        return 0
    for i in range(len(recent) - 1):
        prev = recent.iloc[i]
        curr = recent.iloc[i + 1]
        if prev['close'] < prev[ma_col] and curr['close'] > curr[ma_col]:
            body = abs(curr['close'] - curr['open'])
            if body > 0:
                ratio = (curr['close'] - curr[ma_col]) / body
                if ratio > 0.5:
                    return 1
    return 0

def check_ma_sell(df, ma_col):
    recent = df.tail(MA_CROSS_WINDOW + 1).reset_index(drop=True)
    if len(recent) < 2:
        return 0
    for i in range(len(recent) - 1):
        prev = recent.iloc[i]
        curr = recent.iloc[i + 1]
        if prev['close'] > prev[ma_col] and curr['close'] < curr[ma_col]:
            body = abs(curr['close'] - curr['open'])
            if body > 0:
                ratio = (curr[ma_col] - curr['close']) / body
                if ratio > 0.5:
                    return 1
    return 0

# ================================================================
# پیوت‌ها
# ================================================================
def find_pivots(series, lookback=5):
    pivots_low = []
    pivots_high = []
    for i in range(lookback, len(series) - lookback):
        if series.iloc[i] == series.iloc[i-lookback:i+lookback+1].min():
            pivots_low.append(i)
        if series.iloc[i] == series.iloc[i-lookback:i+lookback+1].max():
            pivots_high.append(i)
    return pivots_low, pivots_high

# ================================================================
# واگرایی (با DIV_RECENCY)
# ================================================================
def check_divergence(df, lookback=DIVERGENCE_LOOKBACK):
    recent = df.tail(lookback).reset_index(drop=True)
    if len(recent) < 30:
        return "none", "none"
    pivots_low, pivots_high = find_pivots(recent['close'], lookback=5)
    div_reg = "none"
    div_hid = "none"
    n = len(recent)
    
    if len(pivots_low) >= 2:
        p2 = pivots_low[-1]
        p1 = pivots_low[-2]
        if (n - 1 - p2) <= DIV_RECENCY and (p2 - p1) >= DIV_MIN_DISTANCE:
            price_down = recent['close'].iloc[p2] < recent['close'].iloc[p1]
            rsi_up = recent['rsi'].iloc[p2] > recent['rsi'].iloc[p1]
            rsi_diff = recent['rsi'].iloc[p2] - recent['rsi'].iloc[p1]
            if price_down and rsi_up and rsi_diff >= DIV_RSI_DIFF:
                if recent['rsi'].iloc[p1] <= DIV_RSI_OVERSOLD:
                    div_reg = "bullish"
            price_up = recent['close'].iloc[p2] > recent['close'].iloc[p1]
            rsi_down = recent['rsi'].iloc[p2] < recent['rsi'].iloc[p1]
            if price_up and rsi_down and abs(rsi_diff) >= DIV_RSI_DIFF:
                div_hid = "bullish"
    
    if len(pivots_high) >= 2:
        p2 = pivots_high[-1]
        p1 = pivots_high[-2]
        if (n - 1 - p2) <= DIV_RECENCY and (p2 - p1) >= DIV_MIN_DISTANCE:
            price_up = recent['close'].iloc[p2] > recent['close'].iloc[p1]
            rsi_down = recent['rsi'].iloc[p2] < recent['rsi'].iloc[p1]
            rsi_diff = recent['rsi'].iloc[p1] - recent['rsi'].iloc[p2]
            if price_up and rsi_down and rsi_diff >= DIV_RSI_DIFF:
                if recent['rsi'].iloc[p1] >= DIV_RSI_OVERBOUGHT:
                    div_reg = "bearish"
            price_down = recent['close'].iloc[p2] < recent['close'].iloc[p1]
            rsi_up = recent['rsi'].iloc[p2] > recent['rsi'].iloc[p1]
            if price_down and rsi_up and abs(rsi_diff) >= DIV_RSI_DIFF:
                div_hid = "bearish"
    return div_reg, div_hid

# ================================================================
# TrendRSI (نسخه ساده - ۲ پیوت آخر)
# ================================================================
def check_rsi_trend_break(df, lookback=DIVERGENCE_LOOKBACK):
    recent = df.tail(lookback).reset_index(drop=True)
    if len(recent) < 30:
        return "none"
    pivots_low, pivots_high = find_pivots(recent['rsi'], lookback=5)
    n = len(recent)
    if len(pivots_high) >= 2:
        p1 = pivots_high[-2]
        p2 = pivots_high[-1]
        if (n - 1 - p2) <= DIV_RECENCY and (p2 - p1) >= DIV_MIN_DISTANCE:
            if recent['rsi'].iloc[p2] < recent['rsi'].iloc[p1]:
                current_rsi = recent['rsi'].iloc[-1]
                if current_rsi > recent['rsi'].iloc[p2]:
                    return "bullish"
    if len(pivots_low) >= 2:
        p1 = pivots_low[-2]
        p2 = pivots_low[-1]
        if (n - 1 - p2) <= DIV_RECENCY and (p2 - p1) >= DIV_MIN_DISTANCE:
            if recent['rsi'].iloc[p2] > recent['rsi'].iloc[p1]:
                current_rsi = recent['rsi'].iloc[-1]
                if current_rsi < recent['rsi'].iloc[p2]:
                    return "bearish"
    return "none"

# ================================================================
# Zone (رفع باگ: کانال از کندل‌های قبل از پنجره شکست)
# ================================================================
def compute_zone_channel(df):
    """محاسبه سقف/کف از ZONE_LOOKBACK کندل قبل از پنجره شکست"""
    needed = ZONE_LOOKBACK + ZONE_BREAK_WINDOW
    if len(df) < needed:
        return None, None
    prev = df.iloc[-(needed):-ZONE_BREAK_WINDOW]
    return prev['high'].max(), prev['low'].min()

def check_zone_break_buy(df):
    high_channel, _ = compute_zone_channel(df)
    if high_channel is None:
        return 0
    threshold = high_channel * (1 + ZONE_BREAK_MARGIN_PCT / 100)
    recent = df.tail(ZONE_BREAK_WINDOW)
    if (recent['close'] > threshold).any():
        return 1
    return 0

def check_zone_break_sell(df):
    _, low_channel = compute_zone_channel(df)
    if low_channel is None:
        return 0
    threshold = low_channel * (1 - ZONE_BREAK_MARGIN_PCT / 100)
    recent = df.tail(ZONE_BREAK_WINDOW)
    if (recent['close'] < threshold).any():
        return 1
    return 0

def get_zone(price, df):
    try:
        high_100, low_100 = compute_zone_channel(df)
        if high_100 is None:
            return "⚪️", "", False
        z_break_buy = check_zone_break_buy(df)
        z_break_sell = check_zone_break_sell(df)
        if z_break_buy:
            return "🟢", "", True
        if z_break_sell:
            return "🟢", "", True
        dist_to_high = (high_100 - price) / high_100 * 100
        dist_to_low = (price - low_100) / low_100 * 100
        if dist_to_high < dist_to_low:
            if dist_to_high <= ZONE_RED_PCT:
                return "🔴", f"{int(100 - dist_to_high)}%", False
            elif dist_to_high <= ZONE_BLUE_PCT:
                return "🔵", f"{int(100 - dist_to_high)}%", False
        else:
            if dist_to_low <= ZONE_RED_PCT:
                return "🔴", f"{int(dist_to_low)}%", False
            elif dist_to_low <= ZONE_BLUE_PCT:
                return "🔵", f"{int(dist_to_low)}%", False
        return "⚪️", "", False
    except Exception as e:
        log_error("get_zone", e)
        return "⚪️", "", False

# ================================================================
# دامیننس (با timestamp)
# ================================================================
def get_market_data():
    result = {}
    try:
        url = "https://api.coingecko.com/api/v3/coins/bitcoin/market_chart?vs_currency=usd&days=1&interval=hourly"
        r = requests.get(url, timeout=15)
        if r.status_code == 200:
            data = r.json()['market_caps']
            if len(data) >= 2:
                result['btc_mc_now'] = data[-1][1]
                result['btc_mc_prev'] = data[-2][1]
    except Exception as e:
        log_error("get_market_data (BTC MC)", e)
    time.sleep(1)
    try:
        url = "https://api.coingecko.com/api/v3/global"
        r = requests.get(url, timeout=15)
        if r.status_code == 200:
            g = r.json()['data']
            pct = g['market_cap_percentage']
            result['btc_dom'] = pct['btc']
            result['eth_dom'] = pct['eth']
            result['usdt_dom'] = pct['usdt']
    except Exception as e:
        log_error("get_market_data (Global)", e)
    result['timestamp'] = time.time()
    return result

def calc_dom_score(current_market, prev_market, btc_trend, symbol):
    score = 0
    details = []
    if not current_market or not prev_market:
        return 0, []
    is_eth = symbol == 'ETH/USDT'
    btc_mc_up = False
    if 'btc_mc_now' in current_market and 'btc_mc_prev' in current_market:
        btc_mc_up = current_market['btc_mc_now'] > current_market['btc_mc_prev']
    btc_dom_up = False
    if 'btc_dom' in current_market and 'btc_dom' in prev_market:
        btc_dom_up = current_market['btc_dom'] > prev_market['btc_dom']
    eth_dom_up = False
    if 'eth_dom' in current_market and 'eth_dom' in prev_market:
        eth_dom_up = current_market['eth_dom'] > prev_market['eth_dom']
    usdt_dom_up = False
    if 'usdt_dom' in current_market and 'usdt_dom' in prev_market:
        usdt_dom_up = current_market['usdt_dom'] > prev_market['usdt_dom']
    
    if btc_mc_up:
        score += 1
        details.append("BTCMC+")
    else:
        score -= 1
        details.append("BTCMC-")
    if btc_trend == 'up' and not btc_dom_up:
        score += 1
        details.append("BTC✓")
    elif btc_trend == 'down' and btc_dom_up:
        score -= 1
        details.append("BTC✗")
    if not is_eth:
        if eth_dom_up:
            score -= 1
            details.append("ETHD-")
        else:
            score += 1
            details.append("ETHD+")
    if usdt_dom_up:
        score -= 1
        details.append("USDT-")
    else:
        score += 1
        details.append("USDT+")
    return score, details

# ================================================================
# سیگنال خرید
# ================================================================
def check_buy_signal(df, div_reg, div_hid, trend_rsi):
    score = 0
    details = {}
    logs = []
    hard_count = 0
    last = df.iloc[-1]
    price = last['close']
    
    dpo_score, dpo_cross = check_dpo_buy(df)
    if dpo_score > 0:
        score += dpo_score
        details['DPO'] = dpo_score
        if dpo_cross:
            hard_count += 1
            logs.append("   ✅ DPO (کراس تازه) [سخت]")
        else:
            logs.append("   ✅ DPO (فقط جهت)")
    else:
        details['DPO'] = 0
    
    if last['rsi'] > last['rsi_ma']:
        score += WEIGHT_RSI
        details['RSI'] = WEIGHT_RSI
        logs.append("   ✅ RSI")
    else:
        details['RSI'] = 0
    
    if div_reg == "bullish":
        score += WEIGHT_DIV_REG
        details['DivReg'] = WEIGHT_DIV_REG
        hard_count += 1
        logs.append("   ✅ DivReg+ [سخت]")
    else:
        details['DivReg'] = 0
    if div_hid == "bullish":
        score += WEIGHT_DIV_HID
        details['DivHid'] = WEIGHT_DIV_HID
        hard_count += 1
        logs.append("   ✅ DivHid+ [سخت]")
    else:
        details['DivHid'] = 0
    
    if trend_rsi == "bullish":
        score += WEIGHT_TREND_RSI
        details['TrendRSI'] = WEIGHT_TREND_RSI
        hard_count += 1
        logs.append("   ✅ TrendRSI+ [سخت]")
    else:
        details['TrendRSI'] = 0
    
    ma50_score = check_ma_buy(df, 'ma_fast')
    if ma50_score > 0:
        score += ma50_score
        details['MA50'] = ma50_score
        hard_count += 1
        logs.append("   ✅ MA50 (کراس تازه) [سخت]")
    else:
        details['MA50'] = 0
    
    ma200_score = check_ma_buy(df, 'ma_slow')
    if ma200_score > 0:
        score += ma200_score
        details['MA200'] = ma200_score
        hard_count += 1
        logs.append("   ✅ MA200 (کراس تازه) [سخت]")
    else:
        details['MA200'] = 0
    
    # ADX با DI
    adx_score = 0
    if last['adx'] > 25 and last['di_plus'] > last['di_minus']:
        adx_score = WEIGHT_ADX
        logs.append("   ✅ ADX (+DI>-DI)")
    details['ADX'] = adx_score
    score += adx_score
    
    # Volume با جهت (کندل سبز)
    vol_score = 0
    if last['volume'] > 1.5 * last['vol_ma'] and last['close'] > last['open']:
        vol_score = WEIGHT_VOLUME
        logs.append("   ✅ Vol (کندل سبز)")
    details['Vol'] = vol_score
    score += vol_score
    
    # BB
    bb_score = 0
    bb_condition = False
    bb_th = last['bb_low'] * 1.02
    if last['close'] < bb_th:
        bb_condition = True
    if last['bb_width'] < BB_SQUEEZE_THRESHOLD and last['close'] > last['bb_mid']:
        bb_condition = True
    if bb_condition:
        bb_score = WEIGHT_BB
        score += bb_score
        logs.append("   ✅ BB/BBSqz")
    details['BB'] = bb_score
    
    # Zone (با باگ رفع‌شده)
    zone_score = 0
    z_color, z_pct, z_break = get_zone(price, df)
    zone_break = check_zone_break_buy(df)
    if z_break or zone_break:
        zone_score = WEIGHT_ZONE
        score += zone_score
        hard_count += 1
        logs.append("   ✅ Zone [سخت]")
    details['Zone'] = zone_score
    
    return score, details, logs, hard_count

# ================================================================
# سیگنال فروش
# ================================================================
def check_sell_signal(df, div_reg, div_hid, trend_rsi):
    score = 0
    details = {}
    logs = []
    hard_count = 0
    last = df.iloc[-1]
    price = last['close']
    
    dpo_score, dpo_cross = check_dpo_sell(df)
    if dpo_score > 0:
        score += dpo_score
        details['DPO'] = dpo_score
        if dpo_cross:
            hard_count += 1
            logs.append("   ✅ DPO (کراس تازه) [سخت]")
        else:
            logs.append("   ✅ DPO (فقط جهت)")
    else:
        details['DPO'] = 0
    
    if last['rsi'] < last['rsi_ma']:
        score += WEIGHT_RSI
        details['RSI'] = WEIGHT_RSI
        logs.append("   ✅ RSI")
    else:
        details['RSI'] = 0
    
    if div_reg == "bearish":
        score += WEIGHT_DIV_REG
        details['DivReg'] = WEIGHT_DIV_REG
        hard_count += 1
        logs.append("   ✅ DivReg- [سخت]")
    else:
        details['DivReg'] = 0
    if div_hid == "bearish":
        score += WEIGHT_DIV_HID
        details['DivHid'] = WEIGHT_DIV_HID
        hard_count += 1
        logs.append("   ✅ DivHid- [سخت]")
    else:
        details['DivHid'] = 0
    
    if trend_rsi == "bearish":
        score += WEIGHT_TREND_RSI
        details['TrendRSI'] = WEIGHT_TREND_RSI
        hard_count += 1
        logs.append("   ✅ TrendRSI- [سخت]")
    else:
        details['TrendRSI'] = 0
    
    ma50_score = check_ma_sell(df, 'ma_fast')
    if ma50_score > 0:
        score += ma50_score
        details['MA50'] = ma50_score
        hard_count += 1
        logs.append("   ✅ MA50 (کراس تازه) [سخت]")
    else:
        details['MA50'] = 0
    
    ma200_score = check_ma_sell(df, 'ma_slow')
    if ma200_score > 0:
        score += ma200_score
        details['MA200'] = ma200_score
        hard_count += 1
        logs.append("   ✅ MA200 (کراس تازه) [سخت]")
    else:
        details['MA200'] = 0
    
    adx_score = 0
    if last['adx'] > 25 and last['di_minus'] > last['di_plus']:
        adx_score = WEIGHT_ADX
        logs.append("   ✅ ADX (-DI>+DI)")
    details['ADX'] = adx_score
    score += adx_score
    
    vol_score = 0
    if last['volume'] > 1.5 * last['vol_ma'] and last['close'] < last['open']:
        vol_score = WEIGHT_VOLUME
        logs.append("   ✅ Vol (کندل قرمز)")
    details['Vol'] = vol_score
    score += vol_score
    
    bb_score = 0
    bb_condition = False
    bb_th = last['bb_high'] * 0.98
    if last['close'] > bb_th:
        bb_condition = True
    if last['bb_width'] < BB_SQUEEZE_THRESHOLD and last['close'] < last['bb_mid']:
        bb_condition = True
    if bb_condition:
        bb_score = WEIGHT_BB
        score += bb_score
        logs.append("   ✅ BB/BBSqz")
    details['BB'] = bb_score
    
    zone_score = 0
    z_color, z_pct, z_break = get_zone(price, df)
    zone_break = check_zone_break_sell(df)
    if z_break or zone_break:
        zone_score = WEIGHT_ZONE
        score += zone_score
        hard_count += 1
        logs.append("   ✅ Zone [سخت]")
    details['Zone'] = zone_score
    
    return score, details, logs, hard_count

# ================================================================
# تأیید ۱ ساعته
# ================================================================
def check_1h_confirmation(exchange, symbol, direction):
    try:
        ohlcv = fetch_ohlcv_safe(exchange, symbol, timeframe='1h', limit=100)
        if not ohlcv or len(ohlcv) < 50:
            return False, "کندل کم"
        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df = df.iloc[:-1].reset_index(drop=True)  # حذف کندل در حال تشکیل
        df['rsi'] = ta.momentum.RSIIndicator(close=df['close'], window=RSI_LENGTH).rsi()
        df['rsi_ma'] = df['rsi'].rolling(window=RSI_MA_LENGTH).mean()
        df['ma_fast'] = df['close'].rolling(window=MA_FAST).mean()
        df['ma_slow'] = df['close'].rolling(window=MA_SLOW).mean()
        last = df.iloc[-1]
        if direction == 'buy':
            ok = last['rsi'] > last['rsi_ma'] and last['ma_fast'] > last['ma_slow']
            return ok, f"RSI {last['rsi']:.0f}"
        else:
            ok = last['rsi'] < last['rsi_ma'] and last['ma_fast'] < last['ma_slow']
            return ok, f"RSI {last['rsi']:.0f}"
    except Exception as e:
        log_error(f"check_1h_confirmation {symbol}", e)
        return False, "خطا"

# ================================================================
# روند BTC
# ================================================================
def get_btc_trend(exchange):
    try:
        ohlcv = fetch_ohlcv_safe(exchange, 'BTC/USDT', timeframe='4h', limit=300)
        if not ohlcv:
            return None
        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df = df.iloc[:-1].reset_index(drop=True)
        df['ma_fast'] = df['close'].rolling(window=50).mean()
        df['ma_slow'] = df['close'].rolling(window=200).mean()
        last = df.iloc[-1]
        return 'up' if last['ma_fast'] > last['ma_slow'] else 'down'
    except Exception as e:
        log_error("get_btc_trend", e)
        return None

# ================================================================
# گرفتن لیست ارزها
# ================================================================
def get_top_symbols(exchange, count=100, min_volume=1000000):
    try:
        tickers = exchange.fetch_tickers()
        usdt_pairs = {}
        for sym, data in tickers.items():
            if sym.endswith('/USDT'):
                clean = sym.replace('/USDT', '')
                if clean in BLACKLIST:
                    continue
                vol = data.get('quoteVolume', 0) or 0
                if vol >= min_volume:
                    usdt_pairs[sym] = vol
        sorted_pairs = sorted(usdt_pairs.items(), key=lambda x: x[1], reverse=True)
        return [sym for sym, _ in sorted_pairs[:count]]
    except Exception as e:
        log_error("get_top_symbols", e)
        return []

# ================================================================
# فرمت پیام (HTML)
# ================================================================
def format_details(details):
    order = ['DPO', 'RSI', 'DivReg', 'DivHid', 'TrendRSI', 'MA50', 'MA200', 'ADX', 'Vol', 'BB', 'Zone']
    parts = []
    for key in order:
        parts.append(f"{key}:{details.get(key, 0)}")
    if details.get('1H', 0) > 0:
        parts.append(f"1H:{details['1H']}")
    if details.get('DOM', 0) != 0:
        parts.append(f"DOM:{details['DOM']:+d}")
    return " | ".join(parts)

def get_legend(details):
    legend = []
    if details.get('DPO', 0) == 3:
        legend.append("DPO:3=کراس تازه")
    elif details.get('DPO', 0) == 1:
        legend.append("DPO:1=فقط جهت")
    if details.get('RSI', 0) > 0:
        legend.append("RSI=بالای MA")
    if details.get('DivReg', 0) > 0:
        legend.append("DivReg=واگرایی معمولی")
    if details.get('DivHid', 0) > 0:
        legend.append("DivHid=واگرایی مخفی")
    if details.get('TrendRSI', 0) > 0:
        legend.append("TrendRSI=شکست خط روند")
    if details.get('MA50', 0) > 0:
        legend.append("MA50=کراس 50")
    if details.get('MA200', 0) > 0:
        legend.append("MA200=کراس 200")
    if details.get('ADX', 0) > 0:
        legend.append("ADX=جهت‌دار")
    if details.get('Vol', 0) > 0:
        legend.append("Vol=حجم+جهت")
    if details.get('BB', 0) > 0:
        legend.append("BB=باند/فشردگی")
    if details.get('Zone', 0) > 0:
        legend.append("Zone=شکست سطح")
    if details.get('DOM', 0) != 0:
        legend.append(f"DOM={details.get('DOM'):+d}")
    return " | ".join(legend) if legend else ""

def make_signal_block(sym, score, details, price, zone_color, zone_pct):
    if score >= THRESHOLD_VERY_STRONG:
        header = "🔥"
    elif score >= THRESHOLD_STRONG:
        header = "⬆️⬆️⬆️"
    elif score >= THRESHOLD_MEDIUM:
        header = "⬆️⬆️"
    else:
        header = "⬆️"
    block = f"\n{header}\n\n<b>#{html.escape(sym)}</b> | {price} | {score}\n"
    block += f"📊 {format_details(details)}\n"
    legend = get_legend(details)
    if legend:
        block += f"🔑 {legend}\n"
    if zone_pct:
        block += f"{zone_color} ({zone_pct})\n"
    else:
        block += f"{zone_color}\n"
    return block

def make_signal_block_sell(sym, score, details, price, zone_color, zone_pct):
    if score >= THRESHOLD_VERY_STRONG:
        header = "🔥"
    elif score >= THRESHOLD_STRONG:
        header = "⬇️⬇️⬇️"
    elif score >= THRESHOLD_MEDIUM:
        header = "⬇️⬇️"
    else:
        header = "⬇️"
    block = f"\n{header}\n\n<b>#{html.escape(sym)}</b> | {price} | {score}\n"
    block += f"📊 {format_details(details)}\n"
    legend = get_legend(details)
    if legend:
        block += f"🔑 {legend}\n"
    if zone_pct:
        block += f"{zone_color} ({zone_pct})\n"
    else:
        block += f"{zone_color}\n"
    return block

# ================================================================
# تابع اصلی
# ================================================================
def main():
    global error_count
    print(f"شروع بررسی - {datetime.now(timezone.utc)}")
    exchange = ccxt.lbank({'enableRateLimit': True})
    
    print("گرفتن دامیننس از CoinGecko...")
    current_market = get_market_data()
    history = load_dom_history()
    prev_market = find_dom_24h_ago(history)
    
    if current_market:
        if 'btc_mc_now' in current_market and 'btc_mc_prev' in current_market:
            print(f"BTC MC: ${current_market['btc_mc_prev']/1e12:.4f}T → ${current_market['btc_mc_now']/1e12:.4f}T")
        if 'btc_dom' in current_market:
            if prev_market and 'btc_dom' in prev_market:
                print(f"BTC Dom: {prev_market['btc_dom']:.2f}% → {current_market['btc_dom']:.2f}%")
            else:
                print(f"BTC Dom: {current_market['btc_dom']:.2f}% (بدون snapshot ۲۴ ساعت قبل)")
    
    btc_trend = get_btc_trend(exchange)
    print(f"BTC Trend: {btc_trend}")
    print()
    
    if current_market:
        history.append(current_market)
        save_dom_history(history)
    
    symbols = get_top_symbols(exchange, TOP_COINS_COUNT, MIN_VOLUME_USDT)
    print(f"تعداد ارزها: {len(symbols)}\n")
    
    buy_signals = []
    sell_signals = []
    debug_count = 0
    core_filtered = 0
    
    for i, symbol in enumerate(symbols, 1):
        try:
            ohlcv = fetch_ohlcv_safe(exchange, symbol, timeframe='4h', limit=400)
            if not ohlcv or len(ohlcv) < 250:
                continue
            
            df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            df = df.iloc[:-1].reset_index(drop=True)  # حذف کندل در حال تشکیل
            df = calculate_indicators(df)
            
            # محاسبه یک‌بار واگرایی و TrendRSI
            div_reg, div_hid = check_divergence(df)
            trend_rsi = check_rsi_trend_break(df)
            
            buy_score, buy_details, buy_logs, buy_hard = check_buy_signal(df, div_reg, div_hid, trend_rsi)
            sell_score, sell_details, sell_logs, sell_hard = check_sell_signal(df, div_reg, div_hid, trend_rsi)
            current_price = df['close'].iloc[-1]
            
            if buy_score >= 1 or sell_score >= 1:
                debug_count += 1
            
            if buy_score >= 1:
                print(f"🔍 {symbol} | BUY | امتیاز: {buy_score} | سخت: {buy_hard}")
            if sell_score >= 1:
                print(f"🔍 {symbol} | SELL | امتیاز: {sell_score} | سخت: {sell_hard}")
            
            if buy_score >= THRESHOLD_WEAK:
                dom_score, dom_details = calc_dom_score(current_market, prev_market, btc_trend, symbol)
                buy_score += dom_score
                buy_details['DOM'] = dom_score
                if dom_details:
                    buy_logs.append(f"   🌐 DOM: {' '.join(dom_details)} ({dom_score:+d})")
            
            if sell_score >= THRESHOLD_WEAK:
                dom_score, dom_details = calc_dom_score(current_market, prev_market, btc_trend, symbol)
                sell_score += -dom_score
                sell_details['DOM'] = -dom_score
                if dom_details:
                    sell_logs.append(f"   🌐 DOM: {' '.join(dom_details)} ({-dom_score:+d})")
            
            if buy_score >= 7:
                buy_logs.append("   ⏳ 1H...")
                confirmed, reason = check_1h_confirmation(exchange, symbol, 'buy')
                if confirmed:
                    buy_score += WEIGHT_1H
                    buy_details['1H'] = WEIGHT_1H
                    buy_logs.append(f"   ✅ 1H ({reason})")
                else:
                    buy_logs.append(f"   ❌ 1H ({reason})")
            
            if sell_score >= 7:
                sell_logs.append("   ⏳ 1H...")
                confirmed, reason = check_1h_confirmation(exchange, symbol, 'sell')
                if confirmed:
                    sell_score += WEIGHT_1H
                    sell_details['1H'] = WEIGHT_1H
                    sell_logs.append(f"   ✅ 1H ({reason})")
                else:
                    sell_logs.append(f"   ❌ 1H ({reason})")
            
            zone_color, zone_pct, _ = get_zone(current_price, df)
            clean_symbol = symbol.replace('/USDT', '')
            
            if buy_score >= THRESHOLD_WEAK and buy_hard >= 2:
                buy_signals.append((clean_symbol, buy_score, buy_details, current_price, zone_color, zone_pct, buy_logs))
            elif buy_score >= THRESHOLD_WEAK and buy_hard < 2:
                core_filtered += 1
            
            if sell_score >= THRESHOLD_WEAK and sell_hard >= 2:
                sell_signals.append((clean_symbol, sell_score, sell_details, current_price, zone_color, zone_pct, sell_logs))
            elif sell_score >= THRESHOLD_WEAK and sell_hard < 2:
                core_filtered += 1
            
            if i % 20 == 0:
                print(f"بررسی {i}/{len(symbols)}")
        except Exception as e:
            log_error(f"main loop {symbol}", e)
            continue
    
    print(f"\n📊 تعداد سیگنال‌های ۱+ : {debug_count}")
    print(f"📊 فیلتر شده (کمتر از ۲ سخت) : {core_filtered}")
    
    print("\n" + "=" * 60)
    print("📋 لاگ:")
    print("=" * 60)
    
    if buy_signals:
        print("\n🟢 خرید:")
        for sym, score, details, price, zc, zp, logs in buy_signals:
            print(f"\n#{sym} | {price} | امتیاز: {score} | {zc} {zp}")
            for log in logs:
                print(log)
    
    if sell_signals:
        print("\n🔴 فروش:")
        for sym, score, details, price, zc, zp, logs in sell_signals:
            print(f"\n#{sym} | {price} | امتیاز: {score} | {zc} {zp}")
            for log in logs:
                print(log)
    
    # ارسال پیام
    if buy_signals or sell_signals:
        header = f"🔔 سیگنال‌های جدید ({datetime.now().strftime('%Y-%m-%d %H:%M')})"
        
        if buy_signals:
            buy_signals.sort(key=lambda x: x[1], reverse=True)
            blocks = []
            for sym, score, details, price, zc, zp, logs in buy_signals[:15]:
                blocks.append(make_signal_block(sym, score, details, price, zc, zp))
            split_and_send(header, blocks, is_buy=True)
        
        if sell_signals:
            sell_signals.sort(key=lambda x: x[1], reverse=True)
            blocks = []
            for sym, score, details, price, zc, zp, logs in sell_signals[:15]:
                blocks.append(make_signal_block_sell(sym, score, details, price, zc, zp))
            split_and_send(header, blocks, is_buy=False)
        
        print(f"\n✅ ارسال شد: {len(buy_signals)} خرید، {len(sell_signals)} فروش")
    else:
        print("\n❌ سیگنال جدیدی پیدا نشد.")
    
    print(f"\n📊 مجموع خطاها: {error_count}")

# ================================================================
# ENTRY POINT
# ================================================================
if __name__ == "__main__":
    main()


# ================================================================
# ================================================================
# بخش ذخیره‌شده برای آینده - همه کامنت شده و غیرفعال
# ================================================================
# ================================================================
#
# ════════════════════════════════════════════════════════════════
# 【نسخه ۶.۰】 تأیید تایم‌فریم روزانه (1D)
# ════════════════════════════════════════════════════════════════
# توضیح: برای هر ارز، علاوه بر ۱H، ۱D هم چک می‌شود.
# شرط: MA50 > MA200 روی کندل‌های بسته‌شده روزانه.
# وزن: WEIGHT_1D = 2
# فراخوانی فقط برای ارزهایی که امتیاز ≥ ۷ دارند.
#
# USE_1D = True
# WEIGHT_1D = 2
#
# def check_1d_confirmation(exchange, symbol, direction):
#     try:
#         ohlcv = fetch_ohlcv_safe(exchange, symbol, timeframe='1d', limit=250)
#         if not ohlcv or len(ohlcv) < 210:
#             return False, "کندل کم"
#         df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
#         df = df.iloc[:-1].reset_index(drop=True)
#         df['ma_fast'] = df['close'].rolling(window=50).mean()
#         df['ma_slow'] = df['close'].rolling(window=200).mean()
#         last = df.iloc[-1]
#         if direction == 'buy':
#             return last['ma_fast'] > last['ma_slow'], "MA50>MA200"
#         else:
#             return last['ma_fast'] < last['ma_slow'], "MA50<MA200"
#     except Exception as e:
#         log_error(f"check_1d_confirmation {symbol}", e)
#         return False, "خطا"
#
# ════════════════════════════════════════════════════════════════
# 【نسخه ۶.۰】 قدرت نسبی نسبت به BTC (RS)
# ════════════════════════════════════════════════════════════════
# توضیح: ratio = close_coin / close_btc (هم‌تراز با timestamp)
# خرید: ratio بالای EMA20 و سقف جدید در ۲۰ کندل اخیر
# فروش: ratio زیر EMA20 و کف جدید در ۲۰ کندل اخیر
# وزن: WEIGHT_RS = 1
# برای BTC و استیبل‌ها اعمال نمی‌شود.
#
# WEIGHT_RS = 1
#
# def check_rs_vs_btc(df_coin, df_btc):
#     try:
#         if df_coin is None or df_btc is None:
#             return 0, 0
#         df_coin = df_coin[['timestamp', 'close']].rename(columns={'close': 'close_coin'})
#         df_btc = df_btc[['timestamp', 'close']].rename(columns={'close': 'close_btc'})
#         merged = pd.merge(df_coin, df_btc, on='timestamp', how='inner')
#         if len(merged) < 25:
#             return 0, 0
#         merged['ratio'] = merged['close_coin'] / merged['close_btc']
#         merged['ema20'] = merged['ratio'].ewm(span=20).mean()
#         last_ratio = merged['ratio'].iloc[-1]
#         last_ema = merged['ema20'].iloc[-1]
#         recent_20 = merged['ratio'].tail(20)
#         buy_rs = (last_ratio > last_ema) and (last_ratio >= recent_20.max() * 0.999)
#         sell_rs = (last_ratio < last_ema) and (last_ratio <= recent_20.min() * 1.001)
#         return (1 if buy_rs else 0), (1 if sell_rs else 0)
#     except Exception as e:
#         log_error("check_rs_vs_btc", e)
#         return 0, 0
#
# ════════════════════════════════════════════════════════════════
# 【نسخه ۶.۰】 Funding Rate (contrarian)
# ════════════════════════════════════════════════════════════════
# توضیح: از صرافی فیوچرز (OKX یا MEXC چون Bybit مسدود است)
# Funding بالا مثبت = ازدحام لانگ = سیگنال مخالف (فروش)
# Funding بالا منفی = ازدحام شورت = سیگنال مخالف (خرید)
# وزن: WEIGHT_FUNDING = 1
# فراخوانی فقط برای ارزهایی که امتیازشان از آستانه ضعیف گذشته.
#
# FUTURES_EXCHANGE = 'okx'  # یا 'mexc'
# FUNDING_LOW = -0.0001
# FUNDING_HIGH = 0.0005
# WEIGHT_FUNDING = 1
#
# def get_funding_scores(exchange_fut, symbols):
#     """گرفتن یک‌جای funding برای همه نمادها"""
#     scores = {}
#     try:
#         rates = exchange_fut.fetch_funding_rates()
#         for sym in symbols:
#             clean = sym.replace('/USDT', '')
#             fut_sym = f"{clean}/USDT:USDT"
#             if fut_sym in rates:
#                 r = rates[fut_sym].get('fundingRate', 0) or 0
#                 scores[sym] = r
#     except Exception as e:
#         log_error("get_funding_scores", e)
#     return scores
#
# def funding_score_for(rate, direction):
#     if rate is None:
#         return 0
#     if direction == 'buy':
#         if rate <= FUNDING_LOW:
#             return 1  # ازدحام شورت → صعود
#         if rate >= FUNDING_HIGH:
#             return -1  # ازدحام لانگ → نزول
#     else:
#         if rate >= FUNDING_HIGH:
#             return 1  # ازدحام لانگ → نزول
#         if rate <= FUNDING_LOW:
#             return -1
#     return 0
#
# ════════════════════════════════════════════════════════════════
# 【نسخه ۶.۰】 Open Interest (OI)
# ════════════════════════════════════════════════════════════════
# توضیح: اگر OI در ۲۴ ساعت اخیر ≥ ۵٪ افزایش یافته و جهت قیمت با سیگنال هم‌جهت است → +۱
# اختیاری. اگر صرافی پشتیبانی نکرد، بی‌صدا رد شود.
#
# USE_OI = True
# WEIGHT_OI = 1
# OI_INCREASE_PCT = 5.0
#
# def check_open_interest(exchange_fut, symbol, direction):
#     try:
#         clean = symbol.replace('/USDT', '')
#         fut_sym = f"{clean}/USDT:USDT"
#         history = exchange_fut.fetch_open_interest_history(fut_sym, timeframe='1h', limit=25)
#         if not history or len(history) < 24:
#             return 0
#         oi_now = history[-1].get('openInterestAmount', 0)
#         oi_24h = history[0].get('openInterestAmount', 0)
#         if oi_24h == 0:
#             return 0
#         increase_pct = (oi_now - oi_24h) / oi_24h * 100
#         if increase_pct >= OI_INCREASE_PCT:
#             # هم‌جهت با سیگنال
#             return 1
#         return 0
#     except Exception as e:
#         # بی‌صدا رد شود
#         return 0
#
# ════════════════════════════════════════════════════════════════
# 【نسخه ۶.۰】 حافظه‌دار کردن سیگنال‌ها + ارزیابی خودکار
# ════════════════════════════════════════════════════════════════
# توضیح: هر سیگنال ارسالی در یک فایل JSON ذخیره می‌شود.
# بعد از ۱۲/۲۴/۷۲ ساعت، قیمت چک می‌شود و سود/ضرر محاسبه می‌شود.
# بعد از چند هفته، وزن‌ها بر اساس داده‌ی واقعی تنظیم می‌شوند.
#
# SIGNAL_HISTORY_FILE = "signal_history.json"
#
# def save_signal_history(symbol, direction, price, score, details):
#     history = load_json(SIGNAL_HISTORY_FILE)
#     if not isinstance(history, list):
#         history = []
#     entry = {
#         "symbol": symbol,
#         "direction": direction,
#         "entry_price": price,
#         "score": score,
#         "details": details,
#         "timestamp": time.time(),
#         "datetime": datetime.now(timezone.utc).isoformat(),
#         "results": {
#             "12h": None,
#             "24h": None,
#             "72h": None
#         }
#     }
#     history.append(entry)
#     save_json(SIGNAL_HISTORY_FILE, history)
#
# def evaluate_pending_signals(exchange):
#     """بررسی سیگنال‌های گذشته و ثبت نتیجه"""
#     history = load_json(SIGNAL_HISTORY_FILE)
#     if not isinstance(history, list):
#         return
#     now = time.time()
#     for entry in history:
#         elapsed_hours = (now - entry['timestamp']) / 3600
#         for window_name, hours in [('12h', 12), ('24h', 24), ('72h', 72)]:
#             if entry['results'].get(window_name) is not None:
#                 continue
#             if elapsed_hours >= hours:
#                 try:
#                     sym = entry['symbol'] + '/USDT'
#                     ohlcv = fetch_ohlcv_safe(exchange, sym, timeframe='1h', limit=int(hours)+5)
#                     if ohlcv:
#                         current_price = ohlcv[-1][4]
#                         entry_price = entry['entry_price']
#                         if entry['direction'] == 'buy':
#                             pnl_pct = (current_price - entry_price) / entry_price * 100
#                         else:
#                             pnl_pct = (entry_price - current_price) / entry_price * 100
#                         entry['results'][window_name] = round(pnl_pct, 2)
#                 except Exception as e:
#                     log_error(f"evaluate {entry['symbol']} {window_name}", e)
#     save_json(SIGNAL_HISTORY_FILE, history)
#
# def get_indicator_performance():
#     """محاسبه عملکرد هر اندیکاتور بر اساس داده‌های تاریخی"""
#     history = load_json(SIGNAL_HISTORY_FILE)
#     if not isinstance(history, list):
#         return {}
#     perf = {}
#     for entry in history:
#         pnl = entry['results'].get('24h')
#         if pnl is None:
#             continue
#         for ind, weight in entry.get('details', {}).items():
#             if weight <= 0:
#                 continue
#             if ind not in perf:
#                 perf[ind] = {"wins": 0, "losses": 0, "total_pnl": 0, "count": 0}
#             perf[ind]["count"] += 1
#             perf[ind]["total_pnl"] += pnl
#             if pnl > 0:
#                 perf[ind]["wins"] += 1
#             else:
#                 perf[ind]["losses"] += 1
#     return perf
#
# ════════════════════════════════════════════════════════════════
# 【ربات ⭐】 ربات جداگانه برای کراس‌های تازه
# ════════════════════════════════════════════════════════════════
# توضیح: ربات جداگانه‌ای که فقط کراس‌های تازه را نشان می‌دهد.
# بدون امتیاز، بدون آستانه، بدون اندیکاتورهای دیگر.
# فقط: DPO کراس تازه، MA50 کراس تازه، MA200 کراس تازه.
#
# - ریپازیتوری جدید: crypto-bot-star
# - فایل: bot_star.py
# - Secrets جدید: TELEGRAM_TOKEN_STAR
# ════════════════════════════════════════════════════════════════
