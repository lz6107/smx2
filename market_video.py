import os
import math
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import requests


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
MARKET_VIDEO_DURATION_SECONDS = env_int("MARKET_VIDEO_DURATION_SECONDS", 70, 65, 75)
MARKET_VIDEO_KEEP_FILES = env_bool("MARKET_VIDEO_KEEP_FILES", "false")
FFMPEG_BINARY = os.getenv("FFMPEG_BINARY", "ffmpeg").strip() or "ffmpeg"
MARKET_VIDEO_DIR = Path(os.getenv("MARKET_VIDEO_DIR", "market_videos"))


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
    ffmpeg_binary = resolve_ffmpeg_binary()

    video_src = f"color=c=0x07111F:s={width}x{height}:r=30:d={duration}"
    audio_src = (
        "aevalsrc=0.014*(sin(2*PI*220*t)+sin(2*PI*277.18*t)+"
        f"sin(2*PI*329.63*t)):s=44100:d={duration}"
    )
    filter_path = str(ass_path).replace("\\", "/").replace(":", "\\:")

    cmd = [
        ffmpeg_binary,
        "-y",
        "-f", "lavfi", "-i", video_src,
        "-f", "lavfi", "-i", audio_src,
        "-vf", f"subtitles={filter_path}",
        "-shortest",
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "96k",
        str(video_path),
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg 生成视频失败: {result.stderr[-1200:]}")


def build_market_video(symbols: List[str], display_map: Dict[str, str]) -> dict:
    width = MARKET_VIDEO_WIDTH
    height = MARKET_VIDEO_HEIGHT
    duration = MARKET_VIDEO_DURATION_SECONDS
    MARKET_VIDEO_DIR.mkdir(parents=True, exist_ok=True)

    market = fetch_enhanced_market_data(symbols, display_map)
    state = market_state(market)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = MARKET_VIDEO_DIR / f"smx2_market_video_{stamp}"
    ass_path = base.with_suffix(".ass")
    video_path = base.with_suffix(".mp4")

    render_ass(market, ass_path, width, height, duration)
    run_ffmpeg(ass_path, video_path, width, height, duration)

    if not MARKET_VIDEO_KEEP_FILES:
        try:
            ass_path.unlink(missing_ok=True)
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
