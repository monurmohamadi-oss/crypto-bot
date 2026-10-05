import ccxt
import pandas as pd
import ta
import requests
import json
import os
import time
from datetime import datetime

TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN', '')
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID', '')

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
WEIGHT_BB_SQZ = 1
WEIGHT_ZONE = 2
WEIGHT_1H = 2

THRESHOLD_WEAK = 5
THRESHOLD_SHOW = 5
THRESHOLD_MEDIUM = 8
THRESHOLD_STRONG = 12
THRESHOLD_VERY_STRONG = 16

TOP_COINS_COUNT = 100
MIN_VOLUME_USDT = 1000000

DIV_MIN_DISTANCE = 5
DIV_RSI_DIFF = 3
DIV_RSI_OVERSOLD = 35
DIV_RSI_OVERBOUGHT = 65

ZONE_RED_PCT = 10
ZONE_BLUE_PCT = 25

BLACKLIST = [
    'USDC', 'USDT', 'USD1', 'DAI', 'FDUSD', 'TUSD', 'BUSD', 'USDD', 'USDP', 'GUSD', 'PYUSD',
    'WBTC', 'WETH', 'STETH', 'WSTETH', 'WBETH', 'RETH', 'CBETH', 'BETH', 'WBNB', 'WAVAX', 'WMATIC',
    'MSTRB', 'NVDAB', 'TSLAB', 'SPCXB', 'COINB', 'HOODB'
]

SIGNALS_FILE = "last_signals.json"
DOM_FILE = "dom_data.json"

def send_telegram(message):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
        r = requests.post(url, json=payload, timeout=15)
        if not r.json().get('ok'):
            print(f"خطا در تلگرام: {r.json()}")
    except Exception as e:
        print(f"خطا: {e}")

def load_json(filename):
    if os.path.exists(filename):
        try:
            with open(filename, 'r') as f:
                return json.load(f)
        except:
            return {}
    return {}

def save_json(filename, data):
    with open(filename, 'w') as f:
        json.dump(data, f, indent=2)

def calculate_dpo(close, period):
    sma_period = period // 2 + 1
    sma = close.rolling(window=sma_period).mean()
    dpo = close - sma
    return dpo.shift(period // 2 + 1)

def calculate_indicators(df):
    df['dpo'] = calculate_dpo(df['close'], DPO_LENGTH)
    df['rsi'] = ta.momentum.RSIIndicator(close=df['close'], window=RSI_LENGTH).rsi()
    df['rsi_ma'] = df['rsi'].rolling(window=RSI_MA_LENGTH).mean()
    df['ma_fast'] = df['close'].rolling(window=MA_FAST).mean()
    df['ma_slow'] = df['close'].rolling(window=MA_SLOW).mean()
    adx_ind = ta.trend.ADXIndicator(high=df['high'], low=df['low'], close=df['close'], window=ADX_LENGTH)
    df['adx'] = adx_ind.adx()
    bb = ta.volatility.BollingerBands(close=df['close'], window=BB_LENGTH, window_dev=BB_STD)
    df['bb_high'] = bb.bollinger_hband()
    df['bb_low'] = bb.bollinger_lband()
    df['bb_mid'] = bb.bollinger_mavg()
    df['bb_width'] = (df['bb_high'] - df['bb_low']) / df['bb_mid']
    df['vol_ma'] = df['volume'].rolling(window=20).mean()
    return df

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

def check_ma_buy(df, ma_col):
    prev = df.iloc[-2]
    last = df.iloc[-1]
    prev_price = prev['close']
    prev_ma = prev[ma_col]
    curr_price = last['close']
    curr_ma = last[ma_col]
    crossed_above = prev_price < prev_ma and curr_price > curr_ma
    body = abs(curr_price - last['open'])
    if body > 0:
        above_ratio = (curr_price - curr_ma) / body
    else:
        above_ratio = 0
    if crossed_above and above_ratio > 0.5:
        return 1
    return 0

def check_ma_sell(df, ma_col):
    prev = df.iloc[-2]
    last = df.iloc[-1]
    prev_price = prev['close']
    prev_ma = prev[ma_col]
    curr_price = last['close']
    curr_ma = last[ma_col]
    crossed_below = prev_price > prev_ma and curr_price < curr_ma
    body = abs(curr_price - last['open'])
    if body > 0:
        below_ratio = (curr_ma - curr_price) / body
    else:
        below_ratio = 0
    if crossed_below and below_ratio > 0.5:
        return 1
    return 0

def find_pivots(series, lookback=5):
    pivots_low = []
    pivots_high = []
    for i in range(lookback, len(series) - lookback):
        if series.iloc[i] == series.iloc[i-lookback:i+lookback+1].min():
            pivots_low.append(i)
        if series.iloc[i] == series.iloc[i-lookback:i+lookback+1].max():
            pivots_high.append(i)
    return pivots_low, pivots_high

def check_divergence(df, lookback=DIVERGENCE_LOOKBACK):
    recent = df.tail(lookback).reset_index(drop=True)
    if len(recent) < 30:
        return "none", "none"
    pivots_low, pivots_high = find_pivots(recent['close'], lookback=5)
    div_reg = "none"
    div_hid = "none"
    
    if len(pivots_low) >= 2:
        p2 = pivots_low[-1]
        p1 = pivots_low[-2]
        if p2 - p1 >= DIV_MIN_DISTANCE:
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
        if p2 - p1 >= DIV_MIN_DISTANCE:
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

def check_rsi_trend_break(df, lookback=DIVERGENCE_LOOKBACK):
    recent = df.tail(lookback).reset_index(drop=True)
    if len(recent) < 30:
        return "none"
    pivots_low, pivots_high = find_pivots(recent['rsi'], lookback=5)
    if len(pivots_high) >= 2:
        p1 = pivots_high[-2]
        p2 = pivots_high[-1]
        if p2 - p1 >= DIV_MIN_DISTANCE:
            if recent['rsi'].iloc[p2] < recent['rsi'].iloc[p1]:
                current_rsi = recent['rsi'].iloc[-1]
                if current_rsi > recent['rsi'].iloc[p2]:
                    return "bullish"
    if len(pivots_low) >= 2:
        p1 = pivots_low[-2]
        p2 = pivots_low[-1]
        if p2 - p1 >= DIV_MIN_DISTANCE:
            if recent['rsi'].iloc[p2] > recent['rsi'].iloc[p1]:
                current_rsi = recent['rsi'].iloc[-1]
                if current_rsi < recent['rsi'].iloc[p2]:
                    return "bearish"
    return "none"

def get_zone(price, df):
    try:
        high_100 = df['high'].tail(100).max()
        low_100 = df['low'].tail(100).min()
        if price > high_100:
            return "🟢", "", True
        if price < low_100:
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
    except:
        return "⚪️", "", False

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
    except:
        pass
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
    except:
        pass
    return result

def calc_dom_score(current_market, previous_market, btc_trend, symbol):
    score = 0
    details = []
    if not current_market or not previous_market:
        return 0, []
    is_eth = symbol == 'ETH/USDT'
    btc_mc_up = False
    if 'btc_mc_now' in current_market and 'btc_mc_prev' in current_market:
        btc_mc_up = current_market['btc_mc_now'] > current_market['btc_mc_prev']
    btc_dom_up = False
    if 'btc_dom' in current_market and 'btc_dom' in previous_market:
        btc_dom_up = current_market['btc_dom'] > previous_market['btc_dom']
    eth_dom_up = False
    if 'eth_dom' in current_market and 'eth_dom' in previous_market:
        eth_dom_up = current_market['eth_dom'] > previous_market['eth_dom']
    usdt_dom_up = False
    if 'usdt_dom' in current_market and 'usdt_dom' in previous_market:
        usdt_dom_up = current_market['usdt_dom'] > previous_market['usdt_dom']
    
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

def check_buy_signal(df):
    score = 0
    details = {}
    logs = []
    last = df.iloc[-1]
    price = last['close']
    
    dpo_score, dpo_cross = check_dpo_buy(df)
    if dpo_score > 0:
        score += dpo_score
        details['DPO'] = dpo_score
        if dpo_cross:
            logs.append("   ✅ DPO (کراس تازه)")
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
    
    div_reg, div_hid = check_divergence(df)
    if div_reg == "bullish":
        score += WEIGHT_DIV_REG
        details['DivReg'] = WEIGHT_DIV_REG
        logs.append("   ✅ DivReg+")
    else:
        details['DivReg'] = 0
    if div_hid == "bullish":
        score += WEIGHT_DIV_HID
        details['DivHid'] = WEIGHT_DIV_HID
        logs.append("   ✅ DivHid+")
    else:
        details['DivHid'] = 0
    
    trend_rsi = check_rsi_trend_break(df)
    if trend_rsi == "bullish":
        score += WEIGHT_TREND_RSI
        details['TrendRSI'] = WEIGHT_TREND_RSI
        logs.append("   ✅ TrendRSI+")
    else:
        details['TrendRSI'] = 0
    
    ma50_score = check_ma_buy(df, 'ma_fast')
    if ma50_score > 0:
        score += ma50_score
        details['MA50'] = ma50_score
        logs.append("   ✅ MA50 (کراس تازه)")
    else:
        details['MA50'] = 0
    
    ma200_score = check_ma_buy(df, 'ma_slow')
    if ma200_score > 0:
        score += ma200_score
        details['MA200'] = ma200_score
        logs.append("   ✅ MA200 (کراس تازه)")
    else:
        details['MA200'] = 0
    
    adx_score = 0
    if last['adx'] > 25:
        adx_score = WEIGHT_ADX
    if adx_score > 0:
        score += adx_score
        logs.append("   ✅ ADX")
    details['ADX'] = adx_score
    
    vol_score = 0
    if last['volume'] > 1.5 * last['vol_ma']:
        vol_score = WEIGHT_VOLUME
        score += vol_score
        logs.append("   ✅ Vol")
    details['Vol'] = vol_score
    
    bb_score = 0
    bb_th = last['bb_low'] * 1.02
    if last['close'] < bb_th:
        bb_score = WEIGHT_BB
        logs.append("   ✅ BB")
    if (div_reg == "bullish" or div_hid == "bullish") and last['close'] < bb_th:
        bb_score = WEIGHT_BB
    if bb_score > 0:
        score += bb_score
    details['BB'] = bb_score
    
    bb_sqz = 0
    if last['bb_width'] < BB_SQUEEZE_THRESHOLD:
        bb_sqz = WEIGHT_BB_SQZ
        score += bb_sqz
        logs.append("   ✅ BBSqz")
    details['BBSqz'] = bb_sqz
    
    zone_score = 0
    z_color, z_pct, z_break = get_zone(price, df)
    if z_break:
        zone_score = WEIGHT_ZONE
        score += zone_score
        logs.append("   ✅ Zone")
    details['Zone'] = zone_score
    
    return score, details, logs

def check_sell_signal(df):
    score = 0
    details = {}
    logs = []
    last = df.iloc[-1]
    price = last['close']
    
    dpo_score, dpo_cross = check_dpo_sell(df)
    if dpo_score > 0:
        score += dpo_score
        details['DPO'] = dpo_score
        if dpo_cross:
            logs.append("   ✅ DPO (کراس تازه)")
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
    
    div_reg, div_hid = check_divergence(df)
    if div_reg == "bearish":
        score += WEIGHT_DIV_REG
        details['DivReg'] = WEIGHT_DIV_REG
        logs.append("   ✅ DivReg-")
    else:
        details['DivReg'] = 0
    if div_hid == "bearish":
        score += WEIGHT_DIV_HID
        details['DivHid'] = WEIGHT_DIV_HID
        logs.append("   ✅ DivHid-")
    else:
        details['DivHid'] = 0
    
    trend_rsi = check_rsi_trend_break(df)
    if trend_rsi == "bearish":
        score += WEIGHT_TREND_RSI
        details['TrendRSI'] = WEIGHT_TREND_RSI
        logs.append("   ✅ TrendRSI-")
    else:
        details['TrendRSI'] = 0
    
    ma50_score = check_ma_sell(df, 'ma_fast')
    if ma50_score > 0:
        score += ma50_score
        details['MA50'] = ma50_score
        logs.append("   ✅ MA50 (کراس تازه)")
    else:
        details['MA50'] = 0
    
    ma200_score = check_ma_sell(df, 'ma_slow')
    if ma200_score > 0:
        score += ma200_score
        details['MA200'] = ma200_score
        logs.append("   ✅ MA200 (کراس تازه)")
    else:
        details['MA200'] = 0
    
    adx_score = 0
    if last['adx'] > 25:
        adx_score = WEIGHT_ADX
    if adx_score > 0:
        score += adx_score
        logs.append("   ✅ ADX")
    details['ADX'] = adx_score
    
    vol_score = 0
    if last['volume'] > 1.5 * last['vol_ma']:
        vol_score = WEIGHT_VOLUME
        score += vol_score
        logs.append("   ✅ Vol")
    details['Vol'] = vol_score
    
    bb_score = 0
    bb_th = last['bb_high'] * 0.98
    if last['close'] > bb_th:
        bb_score = WEIGHT_BB
        logs.append("   ✅ BB")
    if (div_reg == "bearish" or div_hid == "bearish") and last['close'] > bb_th:
        bb_score = WEIGHT_BB
    if bb_score > 0:
        score += bb_score
    details['BB'] = bb_score
    
    bb_sqz = 0
    if last['bb_width'] < BB_SQUEEZE_THRESHOLD:
        bb_sqz = WEIGHT_BB_SQZ
        score += bb_sqz
        logs.append("   ✅ BBSqz")
    details['BBSqz'] = bb_sqz
    
    zone_score = 0
    z_color, z_pct, z_break = get_zone(price, df)
    if z_break:
        zone_score = WEIGHT_ZONE
        score += zone_score
        logs.append("   ✅ Zone")
    details['Zone'] = zone_score
    
    return score, details, logs

def check_1h_confirmation(exchange, symbol, direction):
    try:
        ohlcv = exchange.fetch_ohlcv(symbol, timeframe='1h', limit=100)
        if len(ohlcv) < 50:
            return False, "کندل کم"
        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
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
    except:
        return False, "خطا"

def get_btc_trend(exchange):
    try:
        ohlcv = exchange.fetch_ohlcv('BTC/USDT', timeframe='4h', limit=300)
        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['ma_fast'] = df['close'].rolling(window=50).mean()
        df['ma_slow'] = df['close'].rolling(window=200).mean()
        last = df.iloc[-1]
        return 'up' if last['ma_fast'] > last['ma_slow'] else 'down'
    except:
        return None

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
        print(f"خطا: {e}")
        return []

def format_details(details):
    order = ['DPO', 'RSI', 'DivReg', 'DivHid', 'TrendRSI', 'MA50', 'MA200', 'ADX', 'Vol', 'BB', 'BBSqz', 'Zone']
    parts = []
    for key in order:
        parts.append(f"{key}:{details.get(key, 0)}")
    if details.get('1H', 0) > 0:
        parts.append(f"1H:{details['1H']}")
    if details.get('DOM', 0) != 0:
        parts.append(f"DOM:{details['DOM']:+d}")
    return " | ".join(parts)

def main():
    print(f"شروع بررسی - {datetime.now()}")
    exchange = ccxt.lbank({'enableRateLimit': True})
    
    print("گرفتن دامیننس از CoinGecko...")
    current_market = get_market_data()
    previous_market = load_json(DOM_FILE)
    
    if current_market:
        if 'btc_mc_now' in current_market and 'btc_mc_prev' in current_market:
            print(f"BTC MC: ${current_market['btc_mc_prev']/1e12:.4f}T → ${current_market['btc_mc_now']/1e12:.4f}T")
        if 'btc_dom' in current_market:
            print(f"BTC Dom: {current_market['btc_dom']:.2f}%")
    
    btc_trend = get_btc_trend(exchange)
    print(f"BTC Trend: {btc_trend}")
    print()
    
    if current_market:
        save_json(DOM_FILE, current_market)
    
    symbols = get_top_symbols(exchange, TOP_COINS_COUNT, MIN_VOLUME_USDT)
    print(f"تعداد ارزها: {len(symbols)}\n")
    
    buy_signals = []
    sell_signals = []
    
    debug_count = 0
    
    for i, symbol in enumerate(symbols, 1):
        try:
            ohlcv = exchange.fetch_ohlcv(symbol, timeframe='4h', limit=300)
            if len(ohlcv) < 210:
                continue
            
            df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            df = calculate_indicators(df)
            
            buy_score, buy_details, buy_logs = check_buy_signal(df)
            sell_score, sell_details, sell_logs = check_sell_signal(df)
            current_price = df['close'].iloc[-1]
            
            if buy_score >= 1 or sell_score >= 1:
                debug_count += 1
            
            if buy_score >= 1:
                print(f"🔍 {symbol} | BUY | امتیاز: {buy_score}")
            if sell_score >= 1:
                print(f"🔍 {symbol} | SELL | امتیاز: {sell_score}")
            
            if buy_score >= THRESHOLD_WEAK:
                dom_score, dom_details = calc_dom_score(current_market, previous_market, btc_trend, symbol)
                buy_score += dom_score
                buy_details['DOM'] = dom_score
                if dom_details:
                    buy_logs.append(f"   🌐 DOM: {' '.join(dom_details)} ({dom_score:+d})")
            
            if sell_score >= THRESHOLD_WEAK:
                dom_score, dom_details = calc_dom_score(current_market, previous_market, btc_trend, symbol)
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
            
            if buy_score >= THRESHOLD_WEAK:
                buy_signals.append((clean_symbol, buy_score, buy_details, current_price, zone_color, zone_pct, buy_logs))
            
            if sell_score >= THRESHOLD_WEAK:
                sell_signals.append((clean_symbol, sell_score, sell_details, current_price, zone_color, zone_pct, sell_logs))
            
            if i % 20 == 0:
                print(f"بررسی {i}/{len(symbols)}")
        except:
            continue
    
    print(f"\n📊 تعداد سیگنال‌های ۱+ : {debug_count}")
    
    print("\n" + "="*60)
    print("📋 لاگ:")
    print("="*60)
    
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
    
    if buy_signals or sell_signals:
        buy_signals = buy_signals[:15]
        sell_signals = sell_signals[:15]
        
        if buy_signals:
            message_buy = f"🔔 سیگنال‌های جدید ({datetime.now().strftime('%Y-%m-%d %H:%M')})\n"
            message_buy += "─" * 25 + "\n\n⬆️ *خرید*\n"
            buy_signals.sort(key=lambda x: x[1], reverse=True)
            for sym, score, details, price, zone_color, zone_pct, logs in buy_signals:
                if score >= THRESHOLD_VERY_STRONG:
                    header = "🔥"
                elif score >= THRESHOLD_STRONG:
                    header = "⬆️⬆️⬆️"
                elif score >= THRESHOLD_MEDIUM:
                    header = "⬆️⬆️"
                else:
                    header = "⬆️"
                message_buy += f"\n{header}\n\n#{sym} | {price} | {score}\n"
                message_buy += f"📊 {format_details(details)}\n"
                if zone_pct:
                    message_buy += f"{zone_color} ({zone_pct})\n"
                else:
                    message_buy += f"{zone_color}\n"
            send_telegram(message_buy)
            print("✅ پیام خرید ارسال شد")
        
        if sell_signals:
            message_sell = f"🔔 سیگنال‌های جدید ({datetime.now().strftime('%Y-%m-%d %H:%M')})\n"
            message_sell += "─" * 25 + "\n\n⬇️ *فروش*\n"
            sell_signals.sort(key=lambda x: x[1], reverse=True)
            for sym, score, details, price, zone_color, zone_pct, logs in sell_signals:
                if score >= THRESHOLD_VERY_STRONG:
                    header = "🔥"
                elif score >= THRESHOLD_STRONG:
                    header = "⬇️⬇️⬇️"
                elif score >= THRESHOLD_MEDIUM:
                    header = "⬇️⬇️"
                else:
                    header = "⬇️"
                message_sell += f"\n{header}\n\n#{sym} | {price} | {score}\n"
                message_sell += f"📊 {format_details(details)}\n"
                if zone_pct:
                    message_sell += f"{zone_color} ({zone_pct})\n"
                else:
                    message_sell += f"{zone_color}\n"
            send_telegram(message_sell)
            print("✅ پیام فروش ارسال شد")
        
        print(f"\n✅ ارسال شد: {len(buy_signals)} خرید، {len(sell_signals)} فروش")
    else:
        print("\n❌ سیگنال جدیدی پیدا نشد.")

main()
