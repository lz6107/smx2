import asyncio
import math
import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple, TYPE_CHECKING

import requests
from PIL import Image, ImageDraw, ImageFont

import main as radar

if TYPE_CHECKING:
    from telegram import Bot


PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = Path(os.getenv("V2_OUTPUT_DIR", "v2_outputs"))
IMAGE_DIR = OUTPUT_DIR / "images"
VIDEO_DIR = OUTPUT_DIR / "videos"
FONT_PATH = Path(os.getenv("V2_FONT_PATH", "") or PROJECT_ROOT / "assets" / "fonts" / "NotoSansSC-Bold.ttf")

VALID_ENGINES = {"v1", "v2_shadow", "v2_pilot", "v2_full"}
V2_ENGINE = os.getenv("SMX2_ENGINE", "v1").strip().lower() or "v1"
ENABLE_V2_AI = radar.env_bool("ENABLE_V2_AI", "true")
V2_AI_MODEL = os.getenv("V2_AI_MODEL", "gpt-4.1-mini").strip() or "gpt-4.1-mini"
V2_IMAGE_POSTS_PER_DAY = radar.env_int("V2_IMAGE_POSTS_PER_DAY", 11, 1, 24)
V2_VIDEOS_PER_DAY = radar.env_int("V2_VIDEOS_PER_DAY", 3, 0, 8)
V2_MAX_KEYWORDS = radar.env_int("V2_MAX_KEYWORDS", 5, 3, 5)
V2_PILOT_DAYS = radar.env_int("V2_PILOT_DAYS", 7, 1, 30)
V2_SUCCESS_METRIC = os.getenv("V2_SUCCESS_METRIC", "search_views").strip() or "search_views"

ENABLE_DAILY_LONG_CANDIDATES = radar.env_bool("ENABLE_DAILY_LONG_CANDIDATES", "true")
DAILY_LONG_CANDIDATE_COUNT = radar.env_int("DAILY_LONG_CANDIDATE_COUNT", 5, 1, 8)
DAILY_LONG_HORIZON_HOURS = radar.env_int("DAILY_LONG_HORIZON_HOURS", 48, 12, 168)
DAILY_LONG_MAX_STOP_DISTANCE_PCT = radar.env_int("DAILY_LONG_MAX_STOP_DISTANCE_PCT", 8, 3, 20)
V2_MIN_QUOTE_VOLUME_USDT = float(os.getenv("V2_MIN_QUOTE_VOLUME_USDT", "20000000") or 20000000)

V2_CHECK_INTERVAL_SECONDS = radar.env_int("V2_CHECK_INTERVAL_SECONDS", 30, 10, 300)
V2_MISSED_GRACE_MINUTES = radar.env_int("V2_MISSED_GRACE_MINUTES", 30, 5, 180)
V2_STARTUP_DELAY_SECONDS = radar.env_int("V2_STARTUP_DELAY_SECONDS", 8, 0, 120)
V2_IMAGE_WIDTH = radar.env_int("V2_IMAGE_WIDTH", 1774, 1000, 2400)
V2_IMAGE_HEIGHT = radar.env_int("V2_IMAGE_HEIGHT", 887, 600, 1600)
V2_VIDEO_WIDTH = radar.env_int("V2_VIDEO_WIDTH", 1920, 1280, 3840)
V2_VIDEO_HEIGHT = radar.env_int("V2_VIDEO_HEIGHT", 1080, 720, 2160)
V2_VIDEO_DURATION_SECONDS = radar.env_int("V2_VIDEO_DURATION_SECONDS", 70, 61, 180)
V2_VIDEO_KEEP_FILES = radar.env_bool("V2_VIDEO_KEEP_FILES", "false")
FFMPEG_BINARY = os.getenv("FFMPEG_BINARY", "ffmpeg").strip() or "ffmpeg"

BINANCE_SPOT = "https://api.binance.com"
CORE_SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "DOGEUSDT", "LINKUSDT", "AVAXUSDT", "PEPEUSDT", "WLDUSDT"]
STABLE_BASES = {
    "USDT", "USDC", "BUSD", "FDUSD", "TUSD", "DAI", "USDP", "EUR", "TRY", "BRL", "GBP",
    "PAX", "UST", "USTC", "AEUR", "EURI", "USD1",
}
LEVERAGED_SUFFIXES = ("UP", "DOWN", "BULL", "BEAR")
SEARCH_TERMS = ["BTC", "ETH", "SOL", "USDT", "山寨币", "比特币", "以太坊", "行情", "合约", "现货", "爆仓", "资金流", "待涨币", "开仓价", "止损价"]


@dataclass(frozen=True)
class V2Slot:
    slot_no: int
    slot_key: str
    time: str
    title: str
    theme: str
    keywords: Tuple[str, ...]


IMAGE_SLOTS: List[V2Slot] = [
    V2Slot(1, "v2_morning_main", "08:30", "早盘加密行情主线", "morning", ("BTC", "ETH", "USDT", "行情", "山寨币")),
    V2Slot(2, "v2_btc_usdt", "09:50", "BTC/USDT搜索观察", "btc_usdt", ("BTC", "USDT", "比特币", "行情", "合约")),
    V2Slot(3, "v2_alt_strength", "11:10", "山寨币强弱榜", "alt_strength", ("山寨币", "SOL", "ETH", "USDT", "行情")),
    V2Slot(4, "v2_derivatives_flow", "12:30", "合约资金雷达", "derivatives", ("合约", "爆仓", "BTC", "USDT", "资金流")),
    V2Slot(5, "v2_eth_sol", "14:00", "ETH/SOL联动观察", "eth_sol", ("ETH", "SOL", "以太坊", "USDT", "行情")),
    V2Slot(6, "v2_usdt_flow", "15:30", "USDT资金流观察", "usdt_flow", ("USDT", "资金流", "BTC", "现货", "行情")),
    V2Slot(7, "v2_alt_moves", "17:00", "山寨币异动搜索榜", "alt_moves", ("山寨币", "待涨币", "SOL", "USDT", "行情")),
    V2Slot(8, "v2_daily_longs", "18:30", "5个待涨币观察", "daily_longs", ("待涨币", "开仓价", "止损价", "山寨币", "USDT")),
    V2Slot(9, "v2_night_radar", "20:10", "夜盘行情雷达", "night_radar", ("BTC", "ETH", "SOL", "USDT", "行情")),
    V2Slot(10, "v2_contract_risk", "21:50", "合约风险提示", "contract_risk", ("合约", "爆仓", "止损价", "BTC", "USDT")),
    V2Slot(11, "v2_tomorrow_keywords", "23:10", "明日关键词", "tomorrow", ("BTC", "USDT", "山寨币", "行情", "资金流")),
]

VIDEO_SLOTS: List[V2Slot] = [
    V2Slot(1, "v2_video_search", "10:40", "BTC USDT行情搜索视频", "video_search", ("BTC", "USDT", "行情", "山寨币", "合约")),
    V2Slot(2, "v2_video_alt_visual", "16:20", "山寨币强弱视觉榜", "video_alt", ("山寨币", "待涨币", "SOL", "USDT", "行情")),
    V2Slot(3, "v2_video_night", "22:40", "夜盘BTC资金视频", "video_night", ("BTC", "USDT", "资金流", "合约", "行情")),
]

PILOT_IMAGE_KEYS = {"v2_morning_main", "v2_daily_longs", "v2_tomorrow_keywords"}
PILOT_VIDEO_KEYS = {"v2_video_search"}


def active_image_slots() -> List[V2Slot]:
    slots = IMAGE_SLOTS[:V2_IMAGE_POSTS_PER_DAY]
    if not ENABLE_DAILY_LONG_CANDIDATES:
        slots = [slot for slot in slots if slot.slot_key != "v2_daily_longs"]
    if V2_ENGINE == "v2_pilot":
        slots = [slot for slot in slots if slot.slot_key in PILOT_IMAGE_KEYS]
    return slots


def active_video_slots() -> List[V2Slot]:
    slots = VIDEO_SLOTS[:V2_VIDEOS_PER_DAY]
    if V2_ENGINE == "v2_pilot":
        slots = [slot for slot in slots if slot.slot_key in PILOT_VIDEO_KEYS]
    return slots


def font(size: int) -> ImageFont.FreeTypeFont:
    if not FONT_PATH.exists():
        raise RuntimeError(f"V2 scalable font missing: {FONT_PATH}")
    return ImageFont.truetype(str(FONT_PATH), size)


def now_local() -> datetime:
    return radar.now_local()


def v2_scope() -> str:
    return "shadow" if V2_ENGINE == "v2_shadow" else "live"


def slot_key_for_today(slot: V2Slot, kind: str) -> str:
    return f"{radar.today_key()}:v2:{v2_scope()}:{kind}:{slot.slot_key}"


def init_v2_db() -> None:
    radar.execute(
        """
        CREATE TABLE IF NOT EXISTS v2_post_log (
            id SERIAL PRIMARY KEY,
            post_key TEXT UNIQUE NOT NULL,
            slot_key TEXT NOT NULL,
            slot_time TEXT NOT NULL,
            kind TEXT NOT NULL,
            mode TEXT NOT NULL,
            status TEXT NOT NULL,
            title TEXT,
            content TEXT,
            media_path TEXT,
            error TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        """
    )
    radar.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_v2_post_log_created_at
        ON v2_post_log(created_at);
        """
    )


def v2_log_exists(slot: V2Slot, kind: str) -> bool:
    row = radar.fetch_one(
        "SELECT id FROM v2_post_log WHERE post_key=%s;",
        (slot_key_for_today(slot, kind),),
    )
    return bool(row)


def record_v2_log(slot: V2Slot, kind: str, status: str, content: str = "", media_path: str = "", error: str = "") -> None:
    radar.execute(
        """
        INSERT INTO v2_post_log(post_key, slot_key, slot_time, kind, mode, status, title, content, media_path, error)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT(post_key) DO UPDATE SET
            status=EXCLUDED.status,
            content=EXCLUDED.content,
            media_path=EXCLUDED.media_path,
            error=EXCLUDED.error,
            created_at=NOW();
        """,
        (
            slot_key_for_today(slot, kind),
            slot.slot_key,
            slot.time,
            kind,
            V2_ENGINE,
            status,
            slot.title,
            content[:2500],
            media_path[:600],
            error[:1000],
        ),
    )


def scheduled_today(hhmm: str) -> datetime:
    return radar.scheduled_datetime_today(hhmm)


def due_slots(slots: List[V2Slot], kind: str) -> List[V2Slot]:
    current = now_local()
    due: List[V2Slot] = []
    for slot in slots:
        if v2_log_exists(slot, kind):
            continue

        scheduled = scheduled_today(slot.time)
        grace_until = scheduled + timedelta(minutes=V2_MISSED_GRACE_MINUTES)

        if current < scheduled:
            continue

        if scheduled <= current <= grace_until:
            due.append(slot)
            continue

        if current > grace_until:
            print(f"[v2] skip missed {kind}: {slot.time} {slot.title}")
            record_v2_log(slot, kind, "skipped", error="MISSED_BY_V2_SCHEDULER")
    return due


def safe_float(value, default=None):
    try:
        return float(value)
    except Exception:
        return default


def pct(new: Optional[float], old: Optional[float]) -> Optional[float]:
    if new is None or old in (None, 0):
        return None
    return (new - old) / old * 100


def http_get_json(url: str, params=None, timeout=12):
    r = requests.get(url, params=params, timeout=timeout)
    r.raise_for_status()
    return r.json()


def base_asset(symbol: str) -> str:
    return symbol[:-4] if symbol.endswith("USDT") else symbol


def display_symbol(symbol: str) -> str:
    return base_asset(symbol).upper()


def tradable_usdt_symbol(symbol: str) -> bool:
    if not symbol.endswith("USDT"):
        return False
    base = base_asset(symbol)
    if base in STABLE_BASES:
        return False
    if any(base.endswith(suffix) and len(base) > len(suffix) + 2 for suffix in LEVERAGED_SUFFIXES):
        return False
    if "_" in base or "-" in base:
        return False
    return True


def format_price(price: Optional[float]) -> str:
    return radar.format_price(price)


def format_pct(value: Optional[float]) -> str:
    return radar.format_percent(value)


def volume_text(value: Optional[float]) -> str:
    if value is None:
        return "未知"
    if value >= 1_000_000_000:
        return f"{value / 1_000_000_000:.2f}B"
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    return f"{value:.0f}"


def fetch_klines(symbol: str, interval: str = "1h", limit: int = 48) -> List[dict]:
    rows = http_get_json(
        f"{BINANCE_SPOT}/api/v3/klines",
        params={"symbol": symbol, "interval": interval, "limit": limit},
        timeout=10,
    )
    result = []
    for row in rows:
        result.append({
            "open_time": row[0],
            "open": safe_float(row[1]),
            "high": safe_float(row[2]),
            "low": safe_float(row[3]),
            "close": safe_float(row[4]),
            "volume": safe_float(row[5]),
            "quote_volume": safe_float(row[7]),
        })
    return result


def calc_atr(klines: List[dict], period: int = 14) -> Optional[float]:
    if len(klines) < period + 1:
        return None
    trs = []
    prev_close = klines[-period - 1].get("close")
    for row in klines[-period:]:
        high = row.get("high")
        low = row.get("low")
        close = row.get("close")
        if None in (high, low, close, prev_close):
            return None
        trs.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
        prev_close = close
    return sum(trs) / len(trs) if trs else None


def enrich_from_klines(item: dict, klines: List[dict]) -> dict:
    closes = [row.get("close") for row in klines if row.get("close") is not None]
    if len(closes) >= 2:
        item["change_1h"] = pct(closes[-1], closes[-2])
    if len(closes) >= 5:
        item["change_4h"] = pct(closes[-1], closes[-5])
    if len(closes) >= 25:
        item["change_24h_kline"] = pct(closes[-1], closes[-25])
    item["klines"] = klines
    item["atr_1h"] = calc_atr(klines)
    if klines:
        item["support_6h"] = min(row["low"] for row in klines[-6:] if row.get("low") is not None)
        item["resistance_12h"] = max(row["high"] for row in klines[-12:] if row.get("high") is not None)
    return item


def fetch_market_snapshot() -> dict:
    tickers = http_get_json(f"{BINANCE_SPOT}/api/v3/ticker/24hr", timeout=15)
    universe = []
    by_symbol: Dict[str, dict] = {}

    for raw in tickers:
        symbol = raw.get("symbol", "")
        if not tradable_usdt_symbol(symbol):
            continue
        item = {
            "symbol": symbol,
            "display": display_symbol(symbol),
            "price": safe_float(raw.get("lastPrice")),
            "change_24h": safe_float(raw.get("priceChangePercent")),
            "quote_volume": safe_float(raw.get("quoteVolume")),
            "high_24h": safe_float(raw.get("highPrice")),
            "low_24h": safe_float(raw.get("lowPrice")),
        }
        if not item["price"] or not item["quote_volume"]:
            continue
        by_symbol[symbol] = item
        universe.append(item)

    universe.sort(key=lambda x: x.get("quote_volume") or 0, reverse=True)
    enrich_symbols = list(dict.fromkeys(CORE_SYMBOLS + [item["symbol"] for item in universe[:80]]))
    for symbol in enrich_symbols:
        item = by_symbol.get(symbol)
        if not item:
            continue
        try:
            enrich_from_klines(item, fetch_klines(symbol, "1h", 48))
            time.sleep(0.04)
        except Exception as e:
            print(f"[v2] kline unavailable {symbol}: {e}")

    return {
        "fetched_at": now_local().strftime("%Y-%m-%d %H:%M"),
        "items": by_symbol,
        "universe": universe,
    }


def top_items(snapshot: dict, field: str, limit: int = 5, reverse: bool = True) -> List[dict]:
    items = [item for item in snapshot["items"].values() if item.get(field) is not None]
    items.sort(key=lambda x: x.get(field) or 0, reverse=reverse)
    return items[:limit]


def item(snapshot: dict, symbol: str) -> dict:
    return snapshot["items"].get(symbol, {"symbol": symbol, "display": display_symbol(symbol)})


def breadth(snapshot: dict) -> dict:
    valid = [x for x in snapshot["items"].values() if x.get("change_1h") is not None]
    if not valid:
        return {"up": 0, "down": 0, "avg_1h": None, "state": "观察"}
    up = sum(1 for x in valid if (x.get("change_1h") or 0) > 0)
    down = sum(1 for x in valid if (x.get("change_1h") or 0) < 0)
    avg = sum(x.get("change_1h") or 0 for x in valid) / len(valid)
    if avg > 0.35 and up > down:
        state = "偏强"
    elif avg < -0.35 and down > up:
        state = "偏弱"
    elif up >= down:
        state = "震荡偏多"
    else:
        state = "震荡偏空"
    return {"up": up, "down": down, "avg_1h": avg, "state": state}


def usdt_view(snapshot: dict) -> str:
    active = [x for x in snapshot["universe"][:80] if x.get("quote_volume")]
    if not active:
        return "USDT成交数据不足，先看BTC方向。"
    top_volume = sum(x.get("quote_volume") or 0 for x in active[:10])
    all_volume = sum(x.get("quote_volume") or 0 for x in active)
    concentration = top_volume / all_volume * 100 if all_volume else 0
    if concentration >= 62:
        return f"USDT资金集中在头部币，成交集中度约{concentration:.0f}%，山寨扩散还需要确认。"
    return f"USDT成交分布较分散，头部集中度约{concentration:.0f}%，山寨币有扩散观察价值。"


def market_line(snapshot: dict) -> str:
    b = breadth(snapshot)
    btc = item(snapshot, "BTCUSDT")
    eth = item(snapshot, "ETHUSDT")
    sol = item(snapshot, "SOLUSDT")
    return (
        f"BTC 1h {format_pct(btc.get('change_1h'))}，ETH {format_pct(eth.get('change_1h'))}，"
        f"SOL {format_pct(sol.get('change_1h'))}；市场广度 {b['up']}涨/{b['down']}跌，状态偏{b['state']}。"
    )


def select_daily_long_candidates(snapshot: dict) -> List[dict]:
    btc = item(snapshot, "BTCUSDT")
    btc_4h = btc.get("change_4h") or 0
    scored = []

    for coin in snapshot["universe"][:120]:
        symbol = coin["symbol"]
        if symbol in {"BTCUSDT", "ETHUSDT"}:
            continue
        if (coin.get("quote_volume") or 0) < V2_MIN_QUOTE_VOLUME_USDT:
            continue
        if coin.get("change_1h") is None or coin.get("change_4h") is None:
            continue
        if coin.get("atr_1h") in (None, 0) or not coin.get("support_6h") or not coin.get("resistance_12h"):
            continue

        change_1h = coin.get("change_1h") or 0
        change_4h = coin.get("change_4h") or 0
        change_24h = coin.get("change_24h") or 0
        price = coin.get("price")
        atr = coin.get("atr_1h")
        support = coin.get("support_6h")
        resistance = coin.get("resistance_12h")
        if not price or not atr or not support or not resistance:
            continue
        if change_1h > 4.8 or change_24h > 16:
            continue
        if change_4h < -1.0:
            continue

        entry_low = max(support + atr * 0.25, price - atr * 0.55)
        entry_high = min(price + atr * 0.2, resistance - atr * 0.2)
        if entry_low <= 0 or entry_high <= 0 or entry_low >= entry_high:
            continue

        stop = max(support - atr * 0.45, entry_low * 0.90)
        stop_distance = (entry_low - stop) / entry_low * 100
        if stop_distance <= 0 or stop_distance > DAILY_LONG_MAX_STOP_DISTANCE_PCT:
            continue

        pressure = max(resistance, entry_high + atr)
        relative = change_4h - btc_4h
        volume_score = min(math.log10((coin.get("quote_volume") or 1) / 1_000_000), 3.0)
        pullback_score = max(0, 2.2 - abs((price - entry_low) / price * 100))
        score = change_4h * 1.4 + change_1h * 0.6 + relative * 0.8 + volume_score + pullback_score - stop_distance * 0.25

        candidate = dict(coin)
        candidate.update({
            "entry_low": entry_low,
            "entry_high": entry_high,
            "stop": stop,
            "pressure": pressure,
            "stop_distance": stop_distance,
            "relative_btc_4h": relative,
            "score": score,
        })
        scored.append(candidate)

    scored.sort(key=lambda x: x.get("score") or -999, reverse=True)
    return scored[:DAILY_LONG_CANDIDATE_COUNT]


def keyword_line(keywords: Tuple[str, ...]) -> str:
    return "关键词：" + " ".join(list(keywords)[:V2_MAX_KEYWORDS])


def tag_line(keywords: Tuple[str, ...]) -> str:
    tags = []
    for word in keywords:
        tag = word.replace("/", "").replace(" ", "")
        if tag and len(tags) < 3:
            tags.append(f"#{tag}")
    return " ".join(tags)


def coin_brief(coin: dict) -> str:
    return f"{coin['display']} 1h {format_pct(coin.get('change_1h'))} / 4h {format_pct(coin.get('change_4h'))}"


def daily_longs_text(snapshot: dict, candidates: List[dict]) -> str:
    lines = [
        "【石墨烯财经｜5个待涨币观察】",
        "",
        f"周期：{min(48, DAILY_LONG_HORIZON_HOURS)}小时观察，按现货/低杠杆思路，只做交易计划参考。",
        market_line(snapshot),
        "",
    ]
    if not candidates:
        lines.extend([
            "今日没有筛出合格的待涨币观察标的。",
            "原因通常是成交额不足、K线结构不完整、止损距离过大，或者BTC环境不配合。",
        ])
    else:
        if len(candidates) < DAILY_LONG_CANDIDATE_COUNT:
            lines.append(f"今日只筛出 {len(candidates)} 个合格观察标的，不硬凑5个。")
            lines.append("")
        for idx, coin in enumerate(candidates, 1):
            lines.append(
                f"{idx}. {coin['display']} 现价{format_price(coin.get('price'))}；"
                f"开仓价{format_price(coin['entry_low'])}-{format_price(coin['entry_high'])}；"
                f"止损价{format_price(coin['stop'])}；压力{format_price(coin['pressure'])}；"
                f"逻辑4h {format_pct(coin.get('change_4h'))}、相对BTC {format_pct(coin.get('relative_btc_4h'))}、"
                f"成交{volume_text(coin.get('quote_volume'))}、止损距{coin['stop_distance']:.1f}%。"
            )
        lines.append("")
        lines.append("纪律：只看计划区间，不追拉升，不重仓，不把观察名单当作必涨推荐。")
    lines.extend(["", keyword_line(("待涨币", "开仓价", "止损价", "山寨币", "USDT")), "#待涨币 #山寨币 #USDT"])
    return "\n".join(lines).strip()


def template_post(slot: V2Slot, snapshot: dict, candidates: Optional[List[dict]] = None) -> str:
    btc = item(snapshot, "BTCUSDT")
    eth = item(snapshot, "ETHUSDT")
    sol = item(snapshot, "SOLUSDT")
    strong = top_items(snapshot, "change_4h", 5, True)
    weak = top_items(snapshot, "change_4h", 3, False)
    b = breadth(snapshot)

    if slot.theme == "daily_longs":
        return daily_longs_text(snapshot, candidates or [])

    if slot.theme == "btc_usdt":
        body = [
            f"BTC现价 {format_price(btc.get('price'))}，1h {format_pct(btc.get('change_1h'))}，4h {format_pct(btc.get('change_4h'))}。",
            f"USDT观察：{usdt_view(snapshot)}",
            "石墨烯观察：BTC是搜索流量和盘面方向的共同入口，若BTC不放量，山寨币机会只看局部强弱。",
        ]
    elif slot.theme == "alt_strength":
        rank = "、".join(coin_brief(x) for x in strong[:5]) or "暂无强势山寨币"
        body = [
            f"山寨币强弱榜：{rank}。",
            f"市场广度：{b['up']}涨/{b['down']}跌，平均1h {format_pct(b.get('avg_1h'))}。",
            "石墨烯观察：山寨币行情要看扩散，不看单点拉升；强势币回踩不破才有继续观察价值。",
        ]
    elif slot.theme == "derivatives":
        body = [
            f"合约风险看BTC：1h {format_pct(btc.get('change_1h'))}，24h {format_pct(btc.get('change_24h'))}。",
            f"偏弱名单：{'、'.join(coin_brief(x) for x in weak[:3]) or '暂无明显转弱'}。",
            "石墨烯观察：合约资金最怕追在波动尾端，开仓价和止损价要提前定，不能靠情绪补仓。",
        ]
    elif slot.theme == "eth_sol":
        body = [
            f"ETH：现价 {format_price(eth.get('price'))}，4h {format_pct(eth.get('change_4h'))}。",
            f"SOL：现价 {format_price(sol.get('price'))}，4h {format_pct(sol.get('change_4h'))}。",
            "石墨烯观察：ETH和SOL是山寨币风险偏好的确认器，两者不跟随，山寨强势榜就只能短看。",
        ]
    elif slot.theme == "usdt_flow":
        body = [
            usdt_view(snapshot),
            f"头部行情：BTC {format_pct(btc.get('change_1h'))}，ETH {format_pct(eth.get('change_1h'))}，SOL {format_pct(sol.get('change_1h'))}。",
            "石墨烯观察：USDT资金如果只停在BTC，山寨币不要急；如果成交扩散，待涨币观察名单价值更高。",
        ]
    elif slot.theme == "alt_moves":
        rank = "、".join(coin_brief(x) for x in strong[:5]) or "暂无明显异动"
        body = [
            f"山寨币异动搜索榜：{rank}。",
            "石墨烯观察：异动币只看三件事：成交额是否放大、回踩是否守住、BTC是否拖后腿。",
            "待涨币不是追涨币，开仓价必须靠近支撑，止损价必须提前写出来。",
        ]
    elif slot.theme == "night_radar":
        body = [
            market_line(snapshot),
            f"夜盘强势：{'、'.join(x['display'] for x in strong[:5]) or '暂无'}。",
            "石墨烯观察：夜盘容易放大合约波动，BTC、USDT资金和山寨币强弱榜要一起看。",
        ]
    elif slot.theme == "contract_risk":
        body = [
            f"BTC 4h {format_pct(btc.get('change_4h'))}，山寨平均1h {format_pct(b.get('avg_1h'))}。",
            f"短线偏弱：{'、'.join(coin_brief(x) for x in weak[:3]) or '暂无明显偏弱'}。",
            "石墨烯观察：合约不是看方向就开，止损距离过大时宁可错过，不把波动当机会。",
        ]
    elif slot.theme == "tomorrow":
        body = [
            "明日关键词先看：BTC方向、USDT资金、山寨币强弱、合约风险、待涨币回踩。",
            f"当前盘面：{market_line(snapshot)}",
            "石墨烯观察：明天先确认BTC，再比较ETH/SOL跟随，最后看山寨币是否出现多标的同步放量。",
        ]
    else:
        body = [
            market_line(snapshot),
            f"强势观察：{'、'.join(coin_brief(x) for x in strong[:3]) or '暂无明显强势'}。",
            "石墨烯观察：行情搜索词要落到真实数据，BTC、ETH、SOL、USDT资金和山寨币强弱缺一不可。",
        ]

    return "\n".join([
        f"【石墨烯财经｜{slot.title}】",
        "",
        *body,
        "",
        keyword_line(slot.keywords),
        tag_line(slot.keywords),
    ]).strip()


def call_v2_ai_sync(slot: V2Slot, base_text: str, snapshot: dict) -> Optional[str]:
    if not ENABLE_V2_AI or not radar.OPENAI_API_KEY:
        return None
    try:
        prompt = f"""
你是石墨烯财经Telegram频道编辑。请只基于给定行情事实润色频道文案。
要求：
1. 不编新闻、不编公告、不编爆仓金额、不写必涨必跌。
2. 保留所有价格、涨跌、开仓价、止损价、压力位等数字，不得自行改数字。
3. 显式关键词行最多 {V2_MAX_KEYWORDS} 个词，不要堆叠无关词。
4. 可以强化搜索表达，但不能色情擦边、不能无关热词。
5. 控制在 950 中文字以内，直接输出最终文案。

栏目：{slot.title}
允许核心搜索词：{" ".join(SEARCH_TERMS)}
当前抓取时间：{snapshot.get("fetched_at")}

基础文案：
{base_text}
""".strip()
        payload = {
            "model": V2_AI_MODEL,
            "input": [
                {"role": "system", "content": "你只改写给定事实，不添加新事实。"},
                {"role": "user", "content": prompt},
            ],
            "max_output_tokens": 850,
        }
        r = requests.post(
            "https://api.openai.com/v1/responses",
            headers={"Authorization": f"Bearer {radar.OPENAI_API_KEY}", "Content-Type": "application/json"},
            json=payload,
            timeout=25,
        )
        if r.status_code >= 300:
            print("[v2-ai] OpenAI error:", r.status_code, r.text[:300])
            return None
        text = radar.extract_openai_text(r.json())
        return text.strip() if text else None
    except Exception as e:
        print("[v2-ai] failed:", e)
        return None


async def build_post_content(slot: V2Slot, snapshot: dict, candidates: Optional[List[dict]]) -> str:
    base_text = template_post(slot, snapshot, candidates)
    if slot.theme == "daily_longs":
        return base_text
    ai_text = await asyncio.to_thread(call_v2_ai_sync, slot, base_text, snapshot)
    if ai_text and explicit_keywords_ok(ai_text):
        return ai_text
    if ai_text:
        print(f"[v2-ai] rejected output for {slot.slot_key}: keyword line missing or too long")
    return base_text


def explicit_keywords_ok(text: str) -> bool:
    for line in text.splitlines():
        if line.startswith("关键词："):
            words = [word for word in line.replace("关键词：", "").split() if word.strip()]
            return 1 <= len(words) <= V2_MAX_KEYWORDS
    return False


def draw_text(draw: ImageDraw.ImageDraw, xy: Tuple[int, int], text: str, size: int, fill: str = "#FFFFFF",
              bold: bool = True, max_width: Optional[int] = None, min_size: int = 24) -> None:
    x, y = xy
    size = max(size, min_size)
    while max_width and size > min_size:
        f = font(size)
        if draw.textbbox((x, y), text, font=f)[2] - x <= max_width:
            break
        size -= 2
    draw.text((x, y), text, font=font(size), fill=fill)


def draw_wrapped(draw: ImageDraw.ImageDraw, xy: Tuple[int, int], text: str, size: int, fill: str,
                 max_chars: int, line_gap: int, max_lines: int) -> None:
    lines = []
    current = ""
    for ch in text:
        current += ch
        if len(current) >= max_chars:
            lines.append(current)
            current = ""
        if len(lines) >= max_lines:
            break
    if current and len(lines) < max_lines:
        lines.append(current)
    for i, line in enumerate(lines):
        draw_text(draw, (xy[0], xy[1] + i * line_gap), line, size, fill, False)


def card_background() -> Image.Image:
    img = Image.new("RGB", (V2_IMAGE_WIDTH, V2_IMAGE_HEIGHT), "#07111F")
    draw = ImageDraw.Draw(img)
    for y in range(V2_IMAGE_HEIGHT):
        r = int(7 + y / V2_IMAGE_HEIGHT * 9)
        g = int(17 + y / V2_IMAGE_HEIGHT * 13)
        b = int(31 + y / V2_IMAGE_HEIGHT * 18)
        draw.line([(0, y), (V2_IMAGE_WIDTH, y)], fill=(r, g, b))
    for x in range(0, V2_IMAGE_WIDTH, 118):
        draw.line([(x, 0), (x, V2_IMAGE_HEIGHT)], fill="#16243A")
    for y in range(0, V2_IMAGE_HEIGHT, 92):
        draw.line([(0, y), (V2_IMAGE_WIDTH, y)], fill="#16243A")
    draw.ellipse((V2_IMAGE_WIDTH - 330, -220, V2_IMAGE_WIDTH + 220, 330), fill="#123C59")
    draw.ellipse((-240, V2_IMAGE_HEIGHT - 160, 400, V2_IMAGE_HEIGHT + 480), fill="#0D3D3E")
    return img


def render_post_image(slot: V2Slot, snapshot: dict, candidates: Optional[List[dict]]) -> str:
    IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    path = IMAGE_DIR / f"{radar.today_key()}_{slot.slot_key}.png"
    img = card_background()
    draw = ImageDraw.Draw(img)
    btc = item(snapshot, "BTCUSDT")
    eth = item(snapshot, "ETHUSDT")
    sol = item(snapshot, "SOLUSDT")
    b = breadth(snapshot)

    draw_text(draw, (70, 56), "SMX FINANCE / V2 SEARCH", 30, "#FFD28A", True)
    draw_text(draw, (70, 120), slot.title, 92, "#FFFFFF", True, max_width=1180, min_size=68)
    draw_text(draw, (70, 228), snapshot.get("fetched_at", ""), 30, "#9AA7B8", False)
    draw_text(draw, (70, 270), keyword_line(slot.keywords), 38, "#8AD8FF", True, max_width=1350, min_size=30)

    panels = [(70, 360, 500, 250), (637, 360, 500, 250), (1204, 360, 500, 250)]
    core = [("BTC", btc), ("ETH", eth), ("SOL", sol)]
    for (x, y, w, h), (label, coin) in zip(panels, core):
        draw.rounded_rectangle((x, y, x + w, y + h), radius=24, fill="#0E1D31", outline="#284E76", width=3)
        color = "#19D27F" if (coin.get("change_1h") or 0) >= 0 else "#FF5B73"
        draw_text(draw, (x + 36, y + 30), label, 58, "#FFFFFF", True)
        draw_text(draw, (x + 36, y + 105), format_price(coin.get("price")), 44, "#EAF1FA", True, max_width=230, min_size=32)
        draw_text(draw, (x + 300, y + 46), format_pct(coin.get("change_1h")), 54, color, True, max_width=160, min_size=38)
        draw_text(draw, (x + 304, y + 120), "1H", 28, "#9AA7B8", False)
        draw_text(draw, (x + 36, y + 184), f"4H {format_pct(coin.get('change_4h'))} / 24H {format_pct(coin.get('change_24h'))}", 31, "#9AA7B8", False, max_width=430, min_size=26)

    draw.rounded_rectangle((70, 660, 1704, 820), radius=24, fill="#0E1D31", outline="#284E76", width=3)
    if slot.theme == "daily_longs" and candidates:
        for row, coin in enumerate(candidates[:3]):
            y = 688 + row * 46
            line = (
                f"{coin['display']}  开仓 {format_price(coin['entry_low'])}-{format_price(coin['entry_high'])}  "
                f"止损 {format_price(coin['stop'])}  压力 {format_price(coin['pressure'])}"
            )
            draw_text(draw, (104, y), line, 30, "#FFFFFF", True, max_width=1460, min_size=24)
    else:
        strong = top_items(snapshot, "change_4h", 5, True)
        read = f"市场广度 {b['up']}涨/{b['down']}跌；强势观察 " + "、".join(coin_brief(x) for x in strong[:3])
        draw_wrapped(draw, (104, 694), read, 40, "#FFFFFF", 42, 54, 2)
        draw_text(draw, (104, 780), "观察名单不是必涨推荐，先看BTC和USDT资金环境。", 28, "#FFD28A", False, max_width=1150, min_size=24)

    img.save(path, "PNG")
    return str(path)


def video_caption(slot: V2Slot, snapshot: dict) -> str:
    b = breadth(snapshot)
    return (
        f"【石墨烯财经｜{slot.title}】\n\n"
        f"{market_line(snapshot)}\n"
        f"本条只看真实行情：BTC主线、USDT资金、山寨币强弱、合约风险和开仓止损纪律。"
        f"市场广度 {b['up']}涨/{b['down']}跌，短线别把视觉热度当成必涨信号。\n\n"
        f"{keyword_line(slot.keywords)}\n"
        f"{tag_line(slot.keywords)}"
    )[:1024]


def render_video_frame(slot: V2Slot, snapshot: dict, frame_path: Path, second: int, total: int) -> None:
    img = Image.new("RGB", (V2_VIDEO_WIDTH, V2_VIDEO_HEIGHT), "#07111F")
    draw = ImageDraw.Draw(img)
    for y in range(V2_VIDEO_HEIGHT):
        draw.line([(0, y), (V2_VIDEO_WIDTH, y)], fill=(7, 17 + y // 70, 31 + y // 50))
    draw.ellipse((1340, -200, 2150, 520), fill="#123C59")
    draw.ellipse((-280, 740, 520, 1500), fill="#0D3D3E")
    for x in range(0, V2_VIDEO_WIDTH, 160):
        draw.line([(x, 0), (x, V2_VIDEO_HEIGHT)], fill="#17263D")
    for y in range(0, V2_VIDEO_HEIGHT, 120):
        draw.line([(0, y), (V2_VIDEO_WIDTH, y)], fill="#17263D")

    progress = int((second + 1) / total * (V2_VIDEO_WIDTH - 160))
    draw.rounded_rectangle((80, 1016, V2_VIDEO_WIDTH - 80, 1034), radius=9, fill="#1A2B43")
    draw.rounded_rectangle((80, 1016, 80 + progress, 1034), radius=9, fill="#8AD8FF")

    btc = item(snapshot, "BTCUSDT")
    strong = top_items(snapshot, "change_4h", 5, True)
    b = breadth(snapshot)
    scene = second / max(1, total)

    if scene < 0.33:
        title = "BTC / USDT 行情入口"
        big = f"BTC {format_pct(btc.get('change_1h'))}"
        sub = "先看主线，再看山寨币机会"
    elif scene < 0.66:
        title = "山寨币强弱视觉榜"
        big = " / ".join(x["display"] for x in strong[:3]) or "等待扩散"
        sub = f"市场广度 {b['up']}涨 / {b['down']}跌"
    else:
        title = "开仓止损先写清楚"
        big = "待涨币 ≠ 必涨币"
        sub = "只看计划区间，不追拉升"

    draw_text(draw, (100, 82), "SMX FINANCE / SEARCH VIDEO", 42, "#FFD28A", True)
    draw_text(draw, (100, 176), title, 112, "#FFFFFF", True, max_width=1500, min_size=82)
    draw_text(draw, (100, 370), big, 150, "#8AD8FF", True, max_width=1600, min_size=88)
    draw_text(draw, (108, 570), sub, 76, "#EAF1FA", True, max_width=1450, min_size=58)
    draw_text(draw, (108, 722), keyword_line(slot.keywords), 64, "#FFD28A", True, max_width=1600, min_size=48)
    draw_text(draw, (108, 835), "真实行情参考 / 静音视频 / 不构成投资建议", 48, "#9AA7B8", False, max_width=1500, min_size=40)
    img.save(frame_path, "PNG")


def build_v2_video(slot: V2Slot, snapshot: dict) -> dict:
    VIDEO_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    frames_dir = VIDEO_DIR / f"{slot.slot_key}_{stamp}_frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    video_path = VIDEO_DIR / f"{slot.slot_key}_{stamp}.mp4"
    for second in range(V2_VIDEO_DURATION_SECONDS):
        render_video_frame(slot, snapshot, frames_dir / f"frame_{second:03d}.png", second, V2_VIDEO_DURATION_SECONDS)
    ffmpeg = resolve_ffmpeg_binary()
    cmd = [
        ffmpeg, "-y", "-framerate", "1", "-i", str(frames_dir / "frame_%03d.png"),
        "-vf", "fps=30,format=yuv420p", "-c:v", "libx264", "-preset", "veryfast",
        "-crf", "23", "-movflags", "+faststart", "-an", str(video_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {result.stderr[-1000:]}")
    if not V2_VIDEO_KEEP_FILES:
        shutil.rmtree(frames_dir, ignore_errors=True)
    return {"video_path": str(video_path), "caption": video_caption(slot, snapshot)}


def resolve_ffmpeg_binary() -> str:
    system_ffmpeg = shutil.which(FFMPEG_BINARY)
    if system_ffmpeg:
        return system_ffmpeg
    try:
        import imageio_ffmpeg

        bundled_ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        if bundled_ffmpeg and Path(bundled_ffmpeg).exists():
            return bundled_ffmpeg
    except Exception as e:
        print("[v2-video] imageio-ffmpeg fallback unavailable:", e)
    raise RuntimeError(f"找不到 ffmpeg: {FFMPEG_BINARY}")


async def send_photo(bot: "Bot", content: str, image_path: Optional[str]) -> None:
    if image_path and os.path.isfile(image_path):
        with open(image_path, "rb") as f:
            await bot.send_photo(chat_id=radar.CHAT_ID, photo=f, caption=radar.safe_caption(content))
        return
    await bot.send_message(chat_id=radar.CHAT_ID, text=content, disable_web_page_preview=True)


async def send_video(bot: "Bot", video_path: str, caption: str) -> None:
    with open(video_path, "rb") as f:
        await bot.send_video(
            chat_id=radar.CHAT_ID,
            video=f,
            caption=radar.safe_caption(caption),
            supports_streaming=True,
            width=V2_VIDEO_WIDTH,
            height=V2_VIDEO_HEIGHT,
            duration=V2_VIDEO_DURATION_SECONDS,
        )


async def handle_image_slot(bot: "Bot", slot: V2Slot) -> None:
    media_path = ""
    try:
        print(f"[v2] build image slot={slot.time} {slot.title}")
        snapshot = await asyncio.to_thread(fetch_market_snapshot)
        candidates = await asyncio.to_thread(select_daily_long_candidates, snapshot) if slot.theme == "daily_longs" else None
        content = await build_post_content(slot, snapshot, candidates)
        media_path = await asyncio.to_thread(render_post_image, slot, snapshot, candidates)
        if V2_ENGINE == "v2_shadow":
            print(f"[v2-shadow] image ready slot={slot.slot_key}: {content[:120].replace(chr(10), ' ')}")
            record_v2_log(slot, "image", "shadow", content, media_path)
            return
        await send_photo(bot, content, media_path)
        record_v2_log(slot, "image", "sent", content, media_path)
        print(f"[v2] sent image slot={slot.time} {slot.title}")
    except Exception as e:
        record_v2_log(slot, "image", "failed", media_path=media_path, error=str(e))
        print(f"[v2] image failed slot={slot.slot_key}: {e}")


async def handle_video_slot(bot: "Bot", slot: V2Slot) -> None:
    video_path = ""
    try:
        print(f"[v2] build video slot={slot.time} {slot.title}")
        snapshot = await asyncio.to_thread(fetch_market_snapshot)
        result = await asyncio.to_thread(build_v2_video, slot, snapshot)
        video_path = result["video_path"]
        if V2_ENGINE == "v2_shadow":
            print(f"[v2-shadow] video ready slot={slot.slot_key}: {video_path}")
            record_v2_log(slot, "video", "shadow", result["caption"], video_path)
            return
        await send_video(bot, video_path, result["caption"])
        record_v2_log(slot, "video", "sent", result["caption"], video_path)
        print(f"[v2] sent video slot={slot.time} {slot.title}")
    except Exception as e:
        record_v2_log(slot, "video", "failed", media_path=video_path, error=str(e))
        print(f"[v2] video failed slot={slot.slot_key}: {e}")
    finally:
        if video_path and not V2_VIDEO_KEEP_FILES:
            try:
                Path(video_path).unlink(missing_ok=True)
            except Exception:
                pass


async def v2_scheduler_loop(bot: "Bot") -> None:
    await asyncio.sleep(V2_STARTUP_DELAY_SECONDS)
    print("[v2] scheduler started")
    print("[v2] mode:", V2_ENGINE)
    print("[v2] image slots:", len(active_image_slots()), ", ".join(f"{s.time}/{s.title}" for s in active_image_slots()))
    print("[v2] video slots:", len(active_video_slots()), ", ".join(f"{s.time}/{s.title}" for s in active_video_slots()))
    print("[v2] success metric:", V2_SUCCESS_METRIC, "pilot days:", V2_PILOT_DAYS)
    print("[v2] daily long enabled:", ENABLE_DAILY_LONG_CANDIDATES, "count:", DAILY_LONG_CANDIDATE_COUNT)
    print("[v2] font:", FONT_PATH)

    while True:
        try:
            for slot in due_slots(active_image_slots(), "image"):
                await handle_image_slot(bot, slot)
                await asyncio.sleep(2)
            for slot in due_slots(active_video_slots(), "video"):
                await handle_video_slot(bot, slot)
                await asyncio.sleep(2)
        except Exception as e:
            print("[v2] scheduler error:", e)
        await asyncio.sleep(V2_CHECK_INTERVAL_SECONDS)


def validate_schedule() -> None:
    if len(IMAGE_SLOTS) != 11:
        raise RuntimeError(f"V2 image slot count must be 11, got {len(IMAGE_SLOTS)}")
    if len(VIDEO_SLOTS) != 3:
        raise RuntimeError(f"V2 video slot count must be 3, got {len(VIDEO_SLOTS)}")
    if not FONT_PATH.exists():
        raise RuntimeError(f"V2 font missing: {FONT_PATH}")


def diagnostics() -> dict:
    return {
        "engine": V2_ENGINE,
        "image_slots": len(IMAGE_SLOTS),
        "video_slots": len(VIDEO_SLOTS),
        "active_image_slots": [slot.slot_key for slot in active_image_slots()],
        "active_video_slots": [slot.slot_key for slot in active_video_slots()],
        "max_keywords": V2_MAX_KEYWORDS,
        "daily_long_count": DAILY_LONG_CANDIDATE_COUNT,
        "font_exists": FONT_PATH.exists(),
    }
