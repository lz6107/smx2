import os
import math
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import requests
from PIL import Image, ImageDraw, ImageFont


BINANCE_BASE_URL = "https://api.binance.com"
DEFAULT_VIDEO_TIMES = "09:00,12:00,15:30,20:00,23:20"
VIDEO_CATEGORY = "行情视频"
VIDEO_KEYWORDS = ["BTC", "ETH", "SOL", "USDT资金", "山寨币"]


def env_bool(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


def env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)).strip())
    except Exception:
        value = default
    return max(minimum, min(maximum, value))


ENABLE_MARKET_VIDEOS = env_bool("ENABLE_MARKET_VIDEOS", "false")
MARKET_VIDEO_POST_TIMES = [
    x.strip()
    for x in os.getenv("MARKET_VIDEO_POST_TIMES", DEFAULT_VIDEO_TIMES).split(",")
    if x.strip()
]
MARKET_VIDEO_WIDTH = env_int("MARKET_VIDEO_WIDTH", 1920, 1280, 3840)
MARKET_VIDEO_HEIGHT = env_int("MARKET_VIDEO_HEIGHT", 1080, 720, 2160)
MARKET_VIDEO_DURATION_SECONDS = env_int("MARKET_VIDEO_DURATION_SECONDS", 80, 65, 180)
MARKET_VIDEO_KEEP_FILES = env_bool("MARKET_VIDEO_KEEP_FILES", "false")
FFMPEG_BINARY = os.getenv("FFMPEG_BINARY", "ffmpeg").strip() or "ffmpeg"
MARKET_VIDEO_DIR = Path(os.getenv("MARKET_VIDEO_DIR", "market_videos"))
MARKET_VIDEO_AUDIO_MODE = os.getenv("MARKET_VIDEO_AUDIO_MODE", "silent").strip().lower()


def safe_float(value, default=None):
    try:
        return float(value)
    except Exception:
        return default


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def format_percent(value: Optional[float]) -> str:
    if value is None:
        return "未知"
    sign = "+" if value > 0 else ""
    return f"{sign}{value:.2f}%"


def format_price(value: Optional[float]) -> str:
    if value is None:
        return "未知"
    if value >= 100:
        return f"{value:,.2f}"
    if value >= 1:
        return f"{value:.4f}"
    return f"{value:.8f}"


def format_volume(value: Optional[float]) -> str:
    if value is None:
        return "未知"
    if value >= 1_000_000_000:
        return f"{value / 1_000_000_000:.2f}B"
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"{value / 1_000:.1f}K"
    return f"{value:.0f}"


def format_percent_video(value: Optional[float]) -> str:
    if value is None:
        return "N/A"
    sign = "+" if value > 0 else ""
    return f"{sign}{value:.2f}%"


def format_price_video(value: Optional[float]) -> str:
    if value is None:
        return "N/A"
    if value >= 100:
        return f"{value:,.2f}"
    if value >= 1:
        return f"{value:.4f}"
    return f"{value:.8f}"


def ass_escape(text: str) -> str:
    return (text or "").replace("\\", "\\\\").replace("{", "").replace("}", "").replace("\n", "\\N")


def wrap_text(text: str, max_chars: int = 42, max_lines: int = 2) -> str:
    text = (text or "").strip()
    if len(text) <= max_chars:
        return text

    lines = []
    remaining = text
    split_chars = "。；，、 "

    while remaining and len(lines) < max_lines:
        if len(remaining) <= max_chars:
            lines.append(remaining)
            break

        cut = remaining[:max_chars]
        split_at = max(cut.rfind(ch) for ch in split_chars)
        if split_at < max_chars // 2:
            split_at = max_chars
        lines.append(remaining[:split_at + 1].strip())
        remaining = remaining[split_at + 1:].strip()

    if remaining and lines:
        lines[-1] = lines[-1].rstrip("，、；。 ") + "。"

    return "\n".join(lines[:max_lines])


def ass_color(hex_color: str) -> str:
    color = hex_color.strip().lstrip("#")
    if len(color) != 6:
        color = "FFFFFF"
    rr, gg, bb = color[0:2], color[2:4], color[4:6]
    return f"&H00{bb}{gg}{rr}&"


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
        print("[market-video] imageio-ffmpeg fallback unavailable:", e)

    raise RuntimeError(f"找不到 ffmpeg: {FFMPEG_BINARY}")


def http_get_json(url: str, params=None, timeout=12):
    response = requests.get(url, params=params, timeout=timeout)
    response.raise_for_status()
    return response.json()


def pct_change(start: Optional[float], end: Optional[float]) -> Optional[float]:
    if not start or end is None:
        return None
    return (end - start) / start * 100


def parse_klines(klines: list) -> List[dict]:
    parsed = []
    for row in klines:
        parsed.append({
            "open": safe_float(row[1]),
            "high": safe_float(row[2]),
            "low": safe_float(row[3]),
            "close": safe_float(row[4]),
            "quote_volume": safe_float(row[7], 0.0),
        })
    return parsed


def fetch_enhanced_market_data(symbols: List[str], display_map: Dict[str, str]) -> Dict[str, dict]:
    market: Dict[str, dict] = {}

    for symbol in symbols:
        display = display_map.get(symbol, symbol.replace("USDT", ""))
        item = {
            "symbol": symbol,
            "display": display,
            "price": None,
            "change_1h": None,
            "change_4h": None,
            "change_24h": None,
            "high_24h": None,
            "low_24h": None,
            "quote_volume": None,
            "volume_change": None,
            "closes": [],
        }

        try:
            klines = parse_klines(http_get_json(
                f"{BINANCE_BASE_URL}/api/v3/klines",
                params={"symbol": symbol, "interval": "1h", "limit": 24},
            ))

            closes = [x["close"] for x in klines if x.get("close") is not None]
            highs = [x["high"] for x in klines if x.get("high") is not None]
            lows = [x["low"] for x in klines if x.get("low") is not None]
            volumes = [x.get("quote_volume") or 0 for x in klines]

            if closes:
                item["price"] = closes[-1]
                item["closes"] = closes

            if len(klines) >= 1:
                item["change_1h"] = pct_change(klines[-1].get("open"), klines[-1].get("close"))
            if len(klines) >= 4:
                item["change_4h"] = pct_change(klines[-4].get("open"), klines[-1].get("close"))
            if len(klines) >= 24:
                item["change_24h"] = pct_change(klines[0].get("open"), klines[-1].get("close"))

            item["high_24h"] = max(highs) if highs else None
            item["low_24h"] = min(lows) if lows else None
            item["quote_volume"] = sum(volumes) if volumes else None

            recent_volume = sum(volumes[-4:]) if len(volumes) >= 4 else None
            previous_volume = sum(volumes[-8:-4]) if len(volumes) >= 8 else None
            item["volume_change"] = pct_change(previous_volume, recent_volume)

        except Exception as e:
            print(f"行情视频获取 {symbol} K线失败:", e)

        market[symbol] = item
        time.sleep(0.12)

    return market


def item_by_display(market: Dict[str, dict], display: str) -> dict:
    for item in market.values():
        if item.get("display") == display:
            return item
    return {"display": display}


def top_items(market: Dict[str, dict], field: str, limit: int, reverse: bool = True) -> List[dict]:
    items = [item for item in market.values() if item.get(field) is not None]
    items.sort(key=lambda x: x.get(field) or 0, reverse=reverse)
    return items[:limit]


def market_breadth(market: Dict[str, dict]) -> dict:
    valid = [item for item in market.values() if item.get("change_24h") is not None]
    up = sum(1 for item in valid if item["change_24h"] > 0)
    down = sum(1 for item in valid if item["change_24h"] < 0)
    avg_1h_values = [item["change_1h"] for item in market.values() if item.get("change_1h") is not None]
    avg_1h = sum(avg_1h_values) / len(avg_1h_values) if avg_1h_values else None
    volume_changes = [item["volume_change"] for item in market.values() if item.get("volume_change") is not None]
    avg_volume_change = sum(volume_changes) / len(volume_changes) if volume_changes else None
    return {
        "valid": len(valid),
        "up": up,
        "down": down,
        "avg_1h": avg_1h,
        "avg_volume_change": avg_volume_change,
    }


def market_state(market: Dict[str, dict]) -> dict:
    btc = item_by_display(market, "BTC")
    eth = item_by_display(market, "ETH")
    sol = item_by_display(market, "SOL")
    breadth = market_breadth(market)

    btc_4h = btc.get("change_4h") or 0
    eth_4h = eth.get("change_4h") or 0
    sol_4h = sol.get("change_4h") or 0
    avg_volume = breadth.get("avg_volume_change")

    if btc_4h >= 1.0 and breadth["up"] >= max(5, breadth["down"]):
        state = "偏强"
        headline = "BTC主线偏强，山寨资金尝试扩散"
    elif btc_4h <= -1.0 and breadth["down"] > breadth["up"]:
        state = "偏弱"
        headline = "BTC短线承压，USDT资金偏观望"
    else:
        state = "震荡"
        headline = "BTC区间震荡，USDT资金等待方向"

    if avg_volume is None:
        usdt_view = "USDT交易对成交变化不足，先按观望处理"
    elif avg_volume >= 15:
        usdt_view = "USDT交易对成交放大，短线资金活跃度回升"
    elif avg_volume <= -15:
        usdt_view = "USDT交易对成交降温，短线资金更偏谨慎"
    else:
        usdt_view = "USDT交易对成交平稳，资金暂未明显加速"

    if sol_4h > btc_4h and sol_4h > eth_4h:
        focus = "SOL相对更强，观察山寨是否继续跟随"
    elif eth_4h > btc_4h:
        focus = "ETH相对跟随更好，主流币情绪没有断层"
    else:
        focus = "BTC仍是主线，山寨表现需要看BTC脸色"

    return {
        "state": state,
        "headline": headline,
        "usdt_view": usdt_view,
        "focus": focus,
        "breadth": breadth,
    }


def video_caption(state: dict) -> str:
    keyword_line = " ".join(VIDEO_KEYWORDS[:5])
    return (
        "【石墨烯财经｜行情视频】\n\n"
        f"{state['headline']}。{state['focus']}。\n\n"
        f"关键词：{keyword_line}\n"
        "#BTC #USDT #山寨币"
    )


def make_dialogue(start: str, end: str, style: str, text: str, x: int, y: int, align: int = 7) -> str:
    override = f"{{\\an{align}\\pos({x},{y})}}"
    return f"Dialogue: 2,{start},{end},{style},,0,0,0,,{override}{ass_escape(text)}"


def make_box(start: str, end: str, x: int, y: int, w: int, h: int, color: str, alpha: str = "50") -> str:
    path = f"m {x} {y} l {x + w} {y} l {x + w} {y + h} l {x} {y + h}"
    return (
        f"Dialogue: 0,{start},{end},Draw,,0,0,0,,"
        f"{{\\p1\\an7\\pos(0,0)\\bord0\\shad0\\c{ass_color(color)}\\alpha&H{alpha}&}}{path}"
    )


def make_line(start: str, end: str, points: List[tuple], color: str) -> str:
    if len(points) < 2:
        return ""
    coords = " ".join([("m" if i == 0 else "l") + f" {int(x)} {int(y)}" for i, (x, y) in enumerate(points)])
    return (
        f"Dialogue: 1,{start},{end},Draw,,0,0,0,,"
        f"{{\\p1\\an7\\pos(0,0)\\bord3\\shad0\\c{ass_color(color)}}}{coords}"
    )


def sparkline_points(values: List[float], x: int, y: int, w: int, h: int) -> List[tuple]:
    values = [v for v in values if v is not None]
    if len(values) < 2:
        return []
    lo, hi = min(values), max(values)
    if math.isclose(lo, hi):
        hi = lo + 1
    result = []
    for i, value in enumerate(values):
        px = x + (w * i / max(1, len(values) - 1))
        py = y + h - ((value - lo) / (hi - lo) * h)
        result.append((px, py))
    return result


def trend_color(value: Optional[float]) -> str:
    if value is None:
        return "#9AA7B8"
    return "#19D27F" if value >= 0 else "#FF5B6B"


def hex_to_rgb(hex_color: str) -> tuple:
    color = hex_color.strip().lstrip("#")
    if len(color) != 6:
        color = "FFFFFF"
    return tuple(int(color[i:i + 2], 16) for i in (0, 2, 4))


def load_font(size: int, bold: bool = False):
    candidates = [
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc" if bold else "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc" if bold else "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
        "C:/Windows/Fonts/msyhbd.ttc" if bold else "C:/Windows/Fonts/msyh.ttc",
        "C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf",
    ]

    for path in candidates:
        try:
            if path and Path(path).exists():
                return ImageFont.truetype(path, size)
        except Exception:
            continue

    return ImageFont.load_default()


FONT_CACHE = {}


def font(size: int, bold: bool = False):
    key = (size, bold)
    if key not in FONT_CACHE:
        FONT_CACHE[key] = load_font(size, bold)
    return FONT_CACHE[key]


def draw_text(draw: ImageDraw.ImageDraw, xy: tuple, text: str, size: int, color: str = "#FFFFFF",
              bold: bool = False, anchor: str = "la", max_width: Optional[int] = None):
    value = str(text or "")
    selected_font = font(size, bold)

    if max_width:
        while size > 14:
            box = draw.textbbox((0, 0), value, font=selected_font)
            if box[2] - box[0] <= max_width:
                break
            size -= 2
            selected_font = font(size, bold)

    text_kwargs = {
        "fill": hex_to_rgb(color),
        "font": selected_font,
    }
    if anchor and anchor != "la":
        text_kwargs["anchor"] = anchor
    draw.text(xy, value, **text_kwargs)


def text_size(draw: ImageDraw.ImageDraw, text: str, size: int, bold: bool = False) -> tuple:
    box = draw.textbbox((0, 0), str(text or ""), font=font(size, bold))
    return box[2] - box[0], box[3] - box[1]


def draw_round_rect(draw: ImageDraw.ImageDraw, box: tuple, fill: str, outline: str = "#223A58",
                    radius: int = 18, width: int = 2):
    draw.rounded_rectangle(box, radius=radius, fill=hex_to_rgb(fill), outline=hex_to_rgb(outline), width=width)


def draw_gradient_background(draw: ImageDraw.ImageDraw, width: int, height: int):
    top = hex_to_rgb("#07111F")
    bottom = hex_to_rgb("#101B2D")

    for y in range(height):
        t = y / max(1, height - 1)
        color = tuple(int(top[i] * (1 - t) + bottom[i] * t) for i in range(3))
        draw.line([(0, y), (width, y)], fill=color)

    grid_color = hex_to_rgb("#172A42")
    for x in range(0, width, 120):
        draw.line([(x, 0), (x, height)], fill=grid_color, width=1)
    for y in range(0, height, 90):
        draw.line([(0, y), (width, y)], fill=grid_color, width=1)

    draw.ellipse((width - 470, -220, width + 180, 430), fill=hex_to_rgb("#0E3552"))
    draw.ellipse((-220, height - 360, 520, height + 260), fill=hex_to_rgb("#102E37"))


def english_state(state: dict) -> dict:
    raw = state.get("state")
    if raw == "偏强":
        status = "STRONG"
        headline = "BTC leads. Altcoin flow is expanding"
    elif raw == "偏弱":
        status = "WEAK"
        headline = "BTC under pressure. USDT flow waits"
    else:
        status = "RANGE"
        headline = "BTC range. USDT flow waits for direction"

    breadth = state.get("breadth", {})
    volume = breadth.get("avg_volume_change")
    if volume is None:
        usdt = "USDT volume signal is incomplete"
    elif volume >= 15:
        usdt = "USDT pairs are active. Short-term liquidity is warming"
    elif volume <= -15:
        usdt = "USDT pairs are cooling. Liquidity is more cautious"
    else:
        usdt = "USDT pairs are stable. No clear acceleration yet"

    return {
        "status": status,
        "headline": headline,
        "usdt": usdt,
    }


def draw_panel_title(draw: ImageDraw.ImageDraw, title: str, x: int, y: int):
    draw_text(draw, (x, y), title.upper(), 42, "#8AD8FF", True)
    draw.line((x, y + 58, x + 460, y + 58), fill=hex_to_rgb("#284B70"), width=4)


def draw_sparkline(draw: ImageDraw.ImageDraw, values: List[float], box: tuple, color: str, reveal: float = 1.0):
    x, y, w, h = box
    points = sparkline_points(values, x, y, w, h)
    if len(points) < 2:
        draw.line((x, y + h // 2, x + w, y + h // 2), fill=hex_to_rgb("#31445D"), width=3)
        return

    reveal_count = max(2, min(len(points), int(len(points) * clamp(reveal, 0.05, 1.0))))
    visible = points[:reveal_count]
    draw.line((x, y + h // 2, x + w, y + h // 2), fill=hex_to_rgb("#22364E"), width=2)
    draw.line(visible, fill=hex_to_rgb(color), width=5, joint="curve")
    px, py = visible[-1]
    draw.ellipse((px - 7, py - 7, px + 7, py + 7), fill=hex_to_rgb(color))


def draw_metric_chip(draw: ImageDraw.ImageDraw, x: int, y: int, label: str, value: Optional[float]):
    color = trend_color(value)
    draw_round_rect(draw, (x, y, x + 142, y + 46), "#101F32", "#233C58", 12, 1)
    draw_text(draw, (x + 14, y + 12), label, 19, "#9AA7B8", True)
    draw_text(draw, (x + 60, y + 12), format_percent_video(value), 21, color, True, max_width=70)


def draw_coin_card(draw: ImageDraw.ImageDraw, item: dict, x: int, y: int, w: int, h: int, reveal: float):
    color = trend_color(item.get("change_4h"))
    draw_round_rect(draw, (x, y, x + w, y + h), "#0D1C2E", "#233F5E", 20, 2)
    draw.rectangle((x, y, x + 7, y + h), fill=hex_to_rgb(color))
    draw_text(draw, (x + 28, y + 26), item.get("display", "-"), 44, "#FFFFFF", True)
    draw_text(draw, (x + 164, y + 32), f"${format_price_video(item.get('price'))}", 32, "#EAF1FA", True, max_width=210)
    draw_metric_chip(draw, x + 398, y + 26, "1H", item.get("change_1h"))
    draw_metric_chip(draw, x + 548, y + 26, "4H", item.get("change_4h"))
    draw_sparkline(draw, item.get("closes") or [], (x + 28, y + 94, w - 56, 78), color, reveal)
    draw_text(
        draw,
        (x + 28, y + 190),
        f"24H {format_percent_video(item.get('change_24h'))}  HIGH {format_price_video(item.get('high_24h'))}  LOW {format_price_video(item.get('low_24h'))}",
        23,
        "#9AA7B8",
        False,
        max_width=w - 56,
    )


def draw_breadth_bar(draw: ImageDraw.ImageDraw, breadth: dict, x: int, y: int, w: int, h: int):
    up = breadth.get("up", 0)
    down = breadth.get("down", 0)
    total = max(1, up + down)
    up_w = int(w * up / total)
    draw_round_rect(draw, (x, y, x + w, y + h), "#17263A", "#243C59", h // 2, 1)
    if up_w > 0:
        draw.rounded_rectangle((x, y, x + up_w, y + h), radius=h // 2, fill=hex_to_rgb("#19D27F"))
    if up_w < w:
        draw.rounded_rectangle((x + up_w, y, x + w, y + h), radius=h // 2, fill=hex_to_rgb("#FF5B6B"))
    draw_text(draw, (x, y + h + 20), f"UP {up} / DOWN {down}", 38, "#EAF1FA", True)


def draw_rank_bar(draw: ImageDraw.ImageDraw, item: dict, x: int, y: int, w: int, color: str, index: Optional[int] = None):
    label_prefix = f"{index}. " if index is not None else ""
    value = item.get("change_4h")
    label = f"{label_prefix}{item.get('display', '-')}"
    draw_text(draw, (x, y), label, 44, "#EAF1FA", True, max_width=210)
    draw_text(draw, (x + 230, y + 4), format_percent_video(value), 40, color, True, max_width=160)
    bar_w = int(clamp(abs(value or 0) * 52, 34, w))
    draw_round_rect(draw, (x + 430, y + 16, x + 430 + w, y + 48), "#17263A", "#243C59", 14, 1)
    draw.rounded_rectangle((x + 430, y + 16, x + 430 + bar_w, y + 48), radius=14, fill=hex_to_rgb(color))


def scene_for_second(second: int, duration: int) -> str:
    if second < 12:
        return "overview"
    if second < 30:
        return "majors"
    if second < 45:
        return "flow"
    if second < max(60, duration - 10):
        return "alts"
    return "keywords"


def scene_progress(second: int, duration: int) -> float:
    starts = {
        "overview": 0,
        "majors": 12,
        "flow": 30,
        "alts": 45,
        "keywords": max(60, duration - 10),
    }
    scene = scene_for_second(second, duration)
    start = starts[scene]
    next_start = duration
    for value in sorted(starts.values()):
        if value > start:
            next_start = value
            break
    return clamp((second - start + 1) / max(1, next_start - start), 0.08, 1.0)


def draw_scene_header(draw: ImageDraw.ImageDraw, title: str, subtitle: str, second: int, duration: int):
    draw_text(draw, (72, 54), "SMX FINANCE // MARKET REFERENCE", 38, "#FFD28A", True)
    draw_text(draw, (72, 118), title, 82, "#FFFFFF", True, max_width=1660)
    draw_text(draw, (76, 210), subtitle, 42, "#9AA7B8", True, max_width=1540)
    progress_w = int(1776 * (second + 1) / max(1, duration))
    draw_round_rect(draw, (72, 1018, 1848, 1036), "#17263A", "#243C59", 9, 1)
    draw.rounded_rectangle((72, 1018, 72 + progress_w, 1036), radius=9, fill=hex_to_rgb("#8AD8FF"))


def draw_big_metric(draw: ImageDraw.ImageDraw, box: tuple, label: str, value: str, color: str, note: str = ""):
    x1, y1, x2, y2 = box
    draw_round_rect(draw, box, "#0B1B2C", "#24415F", 26, 3)
    draw_text(draw, (x1 + 38, y1 + 32), label.upper(), 34, "#8AD8FF", True)
    draw_text(draw, (x1 + 38, y1 + 95), value, 76, color, True, max_width=x2 - x1 - 76)
    if note:
        draw_text(draw, (x1 + 38, y2 - 58), note, 32, "#9AA7B8", True, max_width=x2 - x1 - 76)


def render_overview_frame(draw: ImageDraw.ImageDraw, market: Dict[str, dict], second: int, duration: int):
    state = market_state(market)
    view = english_state(state)
    breadth = state["breadth"]
    status_color = "#19D27F" if view["status"] == "STRONG" else "#FF5B6B" if view["status"] == "WEAK" else "#8AD8FF"
    draw_scene_header(draw, "MARKET SNAPSHOT", view["headline"], second, duration)
    draw_big_metric(draw, (90, 330, 610, 650), "STATE", view["status"], status_color, "4H bias from majors")
    draw_big_metric(draw, (700, 330, 1220, 650), "BREADTH", f"{breadth.get('up', 0)} UP / {breadth.get('down', 0)} DOWN", "#FFFFFF", "24H direction count")
    draw_big_metric(draw, (1310, 330, 1830, 650), "USDT FLOW", format_percent_video(breadth.get("avg_volume_change")), "#FFD28A", "4H volume change")
    draw_text(draw, (110, 760), "READ:", 42, "#8AD8FF", True)
    draw_text(draw, (250, 760), "Confirm BTC first, then compare ETH/SOL follow-through.", 48, "#FFFFFF", True, max_width=1480)
    draw_text(draw, (250, 835), "Use altcoin strength as a flow signal, not a single candle chase.", 42, "#9AA7B8", True, max_width=1480)


def render_majors_frame(draw: ImageDraw.ImageDraw, market: Dict[str, dict], second: int, duration: int):
    draw_scene_header(draw, "MAJOR COINS", "BTC / ETH / SOL, 24 x 1H candles", second, duration)
    reveal = scene_progress(second, duration)
    chart_items = [item_by_display(market, "BTC"), item_by_display(market, "ETH"), item_by_display(market, "SOL")]
    y = 305
    for item in chart_items:
        color = trend_color(item.get("change_4h"))
        draw_round_rect(draw, (92, y, 1828, y + 190), "#0B1B2C", "#24415F", 24, 3)
        draw.rectangle((92, y, 104, y + 190), fill=hex_to_rgb(color))
        draw_text(draw, (135, y + 35), item.get("display", "-"), 66, "#FFFFFF", True)
        draw_text(draw, (360, y + 44), f"${format_price_video(item.get('price'))}", 44, "#EAF1FA", True, max_width=360)
        draw_text(draw, (820, y + 32), "1H", 32, "#9AA7B8", True)
        draw_text(draw, (900, y + 32), format_percent_video(item.get("change_1h")), 42, trend_color(item.get("change_1h")), True, max_width=180)
        draw_text(draw, (1100, y + 32), "4H", 32, "#9AA7B8", True)
        draw_text(draw, (1180, y + 32), format_percent_video(item.get("change_4h")), 42, color, True, max_width=180)
        draw_sparkline(draw, item.get("closes") or [], (135, y + 112, 1560, 46), color, reveal)
        y += 225


def render_flow_frame(draw: ImageDraw.ImageDraw, market: Dict[str, dict], second: int, duration: int):
    state = market_state(market)
    view = english_state(state)
    breadth = state["breadth"]
    draw_scene_header(draw, "USDT FLOW", view["usdt"], second, duration)
    draw_round_rect(draw, (120, 340, 1800, 560), "#0B1B2C", "#24415F", 28, 3)
    draw_text(draw, (170, 380), "MARKET BREADTH", 42, "#8AD8FF", True)
    draw_breadth_bar(draw, breadth, 170, 455, 1560, 38)
    draw_big_metric(draw, (120, 610, 620, 850), "AVG 1H", format_percent_video(breadth.get("avg_1h")), "#FFFFFF", "short-term heat")
    draw_big_metric(draw, (710, 610, 1210, 850), "VOLUME FLOW", format_percent_video(breadth.get("avg_volume_change")), "#FFD28A", "USDT pairs")
    draw_big_metric(draw, (1300, 610, 1800, 850), "VALID PAIRS", str(breadth.get("valid", 0)), "#8AD8FF", "tracked symbols")


def render_alts_frame(draw: ImageDraw.ImageDraw, market: Dict[str, dict], second: int, duration: int):
    strong = top_items(market, "change_4h", 5, True)
    weak = [item for item in top_items(market, "change_4h", 10, False) if (item.get("change_4h") or 0) < 0][:4]
    draw_scene_header(draw, "ALTCOIN RANK", "4H strength first, weakness second", second, duration)
    draw_panel_title(draw, "Strength", 135, 310)
    for i, item in enumerate(strong, 1):
        draw_rank_bar(draw, item, 135, 390 + (i - 1) * 92, 330, "#19D27F", i)
    draw_panel_title(draw, "Weakness", 1030, 310)
    if weak:
        for i, item in enumerate(weak, 1):
            draw_rank_bar(draw, item, 1030, 390 + (i - 1) * 92, 330, "#FF5B6B", i)
    else:
        draw_text(draw, (1030, 410), "No clear 4H weakness", 54, "#9AA7B8", True)
    draw_text(draw, (135, 890), "Meaning: rank shows where short-term attention is clustering.", 44, "#FFFFFF", True, max_width=1500)


def render_keywords_scene(draw: ImageDraw.ImageDraw, market: Dict[str, dict], second: int, duration: int):
    state = market_state(market)
    view = english_state(state)
    draw_scene_header(draw, "KEYWORDS", "5 search words, no stacking", second, duration)
    keywords = ["BTC", "ETH", "SOL", "USDT FLOW", "ALTCOINS"]
    colors = ["#8AD8FF", "#19D27F", "#FFD28A", "#FF9F6E", "#EAF1FA"]
    for i, keyword in enumerate(keywords):
        x = 140 + (i % 3) * 560
        y = 360 + (i // 3) * 210
        draw_round_rect(draw, (x, y, x + 460, y + 150), "#0B1B2C", "#24415F", 26, 3)
        draw_text(draw, (x + 230, y + 76), keyword, 56, colors[i], True, anchor="mm", max_width=410)
    draw_text(draw, (140, 850), view["headline"], 48, "#FFFFFF", True, max_width=1550)
    draw_text(draw, (140, 925), "Reference only. Silent data screen. Caption keeps Chinese keywords.", 38, "#9AA7B8", True, max_width=1550)


def render_dashboard_frame(market: Dict[str, dict], frame_path: Path, width: int, height: int,
                           second: int, duration: int) -> None:
    img = Image.new("RGB", (width, height), "#07111F")
    draw = ImageDraw.Draw(img)
    draw_gradient_background(draw, width, height)
    scene = scene_for_second(second, duration)
    if scene == "overview":
        render_overview_frame(draw, market, second, duration)
    elif scene == "majors":
        render_majors_frame(draw, market, second, duration)
    elif scene == "flow":
        render_flow_frame(draw, market, second, duration)
    elif scene == "alts":
        render_alts_frame(draw, market, second, duration)
    else:
        render_keywords_scene(draw, market, second, duration)
    img.save(frame_path, "PNG")


def render_frame_sequence(market: Dict[str, dict], frames_dir: Path, width: int, height: int, duration: int) -> None:
    frames_dir.mkdir(parents=True, exist_ok=True)
    for second in range(duration):
        frame_path = frames_dir / f"frame_{second:03d}.png"
        render_dashboard_frame(market, frame_path, width, height, second, duration)


def run_ffmpeg_from_frames(frames_dir: Path, video_path: Path) -> None:
    ffmpeg_binary = resolve_ffmpeg_binary()
    frame_pattern = str(frames_dir / "frame_%03d.png")
    if MARKET_VIDEO_AUDIO_MODE != "silent":
        print("[market-video] audio mode forced to silent:", MARKET_VIDEO_AUDIO_MODE)

    cmd = [
        ffmpeg_binary,
        "-y",
        "-framerate", "1",
        "-i", frame_pattern,
        "-vf", "fps=30,format=yuv420p",
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "22",
        "-movflags", "+faststart",
        "-an",
        str(video_path),
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg 生成视频失败: {result.stderr[-1200:]}")


def render_ass(market: Dict[str, dict], ass_path: Path, width: int, height: int, duration: int) -> None:
    state = market_state(market)
    strong = top_items(market, "change_4h", 5, True)
    weak = [
        item for item in top_items(market, "change_4h", 10, False)
        if (item.get("change_4h") or 0) < 0
    ][:4]
    btc = item_by_display(market, "BTC")
    eth = item_by_display(market, "ETH")
    sol = item_by_display(market, "SOL")

    start = "0:00:00.00"
    main_end = f"0:01:{min(duration, 65) - 60:02d}.00" if duration >= 60 else f"0:00:{duration:02d}.00"
    keyword_start = "0:01:05.00"
    end = f"0:01:{duration - 60:02d}.00"
    now_text = datetime.now().strftime("%Y-%m-%d %H:%M")

    events = []
    events.append(make_box(start, end, 0, 0, width, height, "#07111F", "00"))
    events.append(make_box(start, end, 36, 36, width - 72, height - 72, "#101B2D", "18"))
    events.append(make_box(start, main_end, 52, 118, 725, 735, "#0F2138", "18"))
    events.append(make_box(start, main_end, 805, 118, 510, 735, "#0F2138", "18"))
    events.append(make_box(start, main_end, 1342, 118, 525, 735, "#0F2138", "18"))
    events.append(make_box(start, main_end, 52, 878, 1815, 150, "#13263F", "10"))

    events.append(make_dialogue(start, main_end, "Brand", "石墨烯财经｜横屏行情参考", 58, 52))
    events.append(make_dialogue(start, main_end, "Muted", now_text, width - 330, 56))
    events.append(make_dialogue(start, main_end, "Title", state["headline"], 58, 96))

    chart_items = [btc, eth, sol]
    y0 = 175
    for index, item in enumerate(chart_items):
        y = y0 + index * 215
        display = item.get("display", "-")
        color = trend_color(item.get("change_4h"))
        events.append(make_dialogue(start, main_end, "Coin", display, 82, y))
        events.append(make_dialogue(start, main_end, "Data", f"价格 {format_price(item.get('price'))}", 210, y + 4))
        events.append(make_dialogue(start, main_end, "Small", f"1h {format_percent(item.get('change_1h'))}", 82, y + 65))
        events.append(make_dialogue(start, main_end, "Small", f"4h {format_percent(item.get('change_4h'))}", 250, y + 65))
        events.append(make_dialogue(start, main_end, "Small", f"24h {format_percent(item.get('change_24h'))}", 420, y + 65))
        points = sparkline_points(item.get("closes") or [], 82, y + 112, 620, 70)
        line = make_line(start, main_end, points, color)
        if line:
            events.append(line)
        events.append(make_dialogue(start, main_end, "Muted", f"24h 高 {format_price(item.get('high_24h'))} / 低 {format_price(item.get('low_24h'))}", 82, y + 190))

    breadth = state["breadth"]
    events.append(make_dialogue(start, main_end, "PanelTitle", "市场状态", 830, 170))
    events.append(make_dialogue(start, main_end, "BigState", state["state"], 830, 220))
    events.append(make_dialogue(start, main_end, "Data", f"上涨 {breadth['up']} / 下跌 {breadth['down']}", 830, 335))
    events.append(make_dialogue(start, main_end, "Data", f"平均1h {format_percent(breadth.get('avg_1h'))}", 830, 405))
    events.append(make_dialogue(start, main_end, "Data", f"成交变化 {format_percent(breadth.get('avg_volume_change'))}", 830, 475))
    events.append(make_dialogue(start, main_end, "PanelTitle", "USDT资金观察", 830, 585))
    events.append(make_dialogue(start, main_end, "Body", state["usdt_view"], 830, 640))
    events.append(make_dialogue(start, main_end, "Body", state["focus"], 830, 735))

    events.append(make_dialogue(start, main_end, "PanelTitle", "山寨强弱榜 4h", 1370, 170))
    for i, item in enumerate(strong):
        y = 238 + i * 70
        label = f"{i + 1}. {item['display']}  {format_percent(item.get('change_4h'))}"
        events.append(make_dialogue(start, main_end, "RankGreen", label, 1370, y))
        bar_w = int(clamp(abs(item.get("change_4h") or 0) * 32, 18, 250))
        events.append(make_box(start, main_end, 1600, y + 10, bar_w, 18, "#19D27F", "18"))

    events.append(make_dialogue(start, main_end, "PanelTitle", "短线偏弱", 1370, 610))
    if weak:
        for i, item in enumerate(weak):
            y = 680 + i * 62
            label = f"{item['display']}  {format_percent(item.get('change_4h'))}"
            events.append(make_dialogue(start, main_end, "RankRed", label, 1370, y))
    else:
        events.append(make_dialogue(start, main_end, "Muted", "暂无明显4h转弱币种", 1370, 680))

    observation = wrap_text(
        f"{state['headline']}。"
        f"{state['usdt_view']}。"
        "观察BTC能否维持主线，ETH与SOL是否继续跟随，山寨币只看强弱扩散，不看单点拉升。",
        max_chars=46,
        max_lines=2,
    )
    events.append(make_dialogue(start, main_end, "Conclusion", observation, 82, 908))
    events.append(make_dialogue(start, main_end, "Keyword", "关键词：BTC ETH SOL USDT资金 山寨币", 82, 988))

    events.append(make_box(keyword_start, end, 0, 0, width, height, "#07111F", "00"))
    events.append(make_dialogue(keyword_start, end, "Brand", "石墨烯财经｜行情视频关键词", 80, 86))
    events.append(make_dialogue(keyword_start, end, "Title", "本条视频只看这5个词", 80, 170))
    for i, keyword in enumerate(VIDEO_KEYWORDS[:5]):
        events.append(make_dialogue(keyword_start, end, "KeywordLarge", keyword, 200 + i * 310, 460, align=8))
    events.append(make_dialogue(keyword_start, end, "Body", state["headline"], 80, 740))
    events.append(make_dialogue(keyword_start, end, "Conclusion", "不构成投资建议，只做行情参考。", 80, 900))

    ass = f"""[Script Info]
Title: SMX2 Market Video
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
ScaledBorderAndShadow: yes
WrapStyle: 2

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Draw,Noto Sans CJK SC,1,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1
Style: Brand,Noto Sans CJK SC,34,&H00FFD28A,&H000000FF,&H000A1220,&H00000000,1,0,0,0,100,100,0,0,1,1,0,7,0,0,0,1
Style: Title,Noto Sans CJK SC,56,&H00FFFFFF,&H000000FF,&H000A1220,&H00000000,1,0,0,0,100,100,0,0,1,2,0,7,0,0,0,1
Style: PanelTitle,Noto Sans CJK SC,32,&H008AD8FF,&H000000FF,&H000A1220,&H00000000,1,0,0,0,100,100,0,0,1,1,0,7,0,0,0,1
Style: Coin,Noto Sans CJK SC,46,&H00FFFFFF,&H000000FF,&H000A1220,&H00000000,1,0,0,0,100,100,0,0,1,1,0,7,0,0,0,1
Style: Data,Noto Sans CJK SC,32,&H00FFFFFF,&H000000FF,&H000A1220,&H00000000,1,0,0,0,100,100,0,0,1,1,0,7,0,0,0,1
Style: Small,Noto Sans CJK SC,28,&H00C7D4E6,&H000000FF,&H000A1220,&H00000000,0,0,0,0,100,100,0,0,1,1,0,7,0,0,0,1
Style: Muted,Noto Sans CJK SC,24,&H009AA7B8,&H000000FF,&H000A1220,&H00000000,0,0,0,0,100,100,0,0,1,1,0,7,0,0,0,1
Style: Body,Noto Sans CJK SC,31,&H00EAF1FA,&H000000FF,&H000A1220,&H00000000,0,0,0,0,100,100,0,0,1,1,0,7,0,0,0,1
Style: BigState,Noto Sans CJK SC,80,&H0019D27F,&H000000FF,&H000A1220,&H00000000,1,0,0,0,100,100,0,0,1,2,0,7,0,0,0,1
Style: RankGreen,Noto Sans CJK SC,30,&H0019D27F,&H000000FF,&H000A1220,&H00000000,1,0,0,0,100,100,0,0,1,1,0,7,0,0,0,1
Style: RankRed,Noto Sans CJK SC,29,&H006B5BFF,&H000000FF,&H000A1220,&H00000000,1,0,0,0,100,100,0,0,1,1,0,7,0,0,0,1
Style: Conclusion,Noto Sans CJK SC,34,&H00FFFFFF,&H000000FF,&H000A1220,&H00000000,1,0,0,0,100,100,0,0,1,1,0,7,0,0,0,1
Style: Keyword,Noto Sans CJK SC,32,&H008AD8FF,&H000000FF,&H000A1220,&H00000000,1,0,0,0,100,100,0,0,1,1,0,7,0,0,0,1
Style: KeywordLarge,Noto Sans CJK SC,50,&H008AD8FF,&H000000FF,&H000A1220,&H00000000,1,0,0,0,100,100,0,0,1,2,0,8,0,0,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    ass_path.write_text(ass + "\n".join([event for event in events if event]) + "\n", encoding="utf-8")


def run_ffmpeg(ass_path: Path, video_path: Path, width: int, height: int, duration: int) -> None:
    raise RuntimeError("legacy ASS renderer is disabled; use run_ffmpeg_from_frames")


def build_market_video(symbols: List[str], display_map: Dict[str, str]) -> dict:
    width = MARKET_VIDEO_WIDTH
    height = MARKET_VIDEO_HEIGHT
    duration = MARKET_VIDEO_DURATION_SECONDS
    MARKET_VIDEO_DIR.mkdir(parents=True, exist_ok=True)

    market = fetch_enhanced_market_data(symbols, display_map)
    state = market_state(market)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = MARKET_VIDEO_DIR / f"smx2_market_video_{stamp}"
    frames_dir = MARKET_VIDEO_DIR / f"{base.name}_frames"
    video_path = base.with_suffix(".mp4")

    render_frame_sequence(market, frames_dir, width, height, duration)
    run_ffmpeg_from_frames(frames_dir, video_path)

    if not MARKET_VIDEO_KEEP_FILES:
        try:
            shutil.rmtree(frames_dir, ignore_errors=True)
        except Exception:
            pass

    return {
        "video_path": str(video_path),
        "caption": video_caption(state),
        "headline": state["headline"],
        "keywords": VIDEO_KEYWORDS[:5],
    }


def market_video_slot_key(slot_time: str) -> str:
    return f"market_video_{slot_time.replace(':', '')}"
