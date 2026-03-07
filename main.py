"""
UKD Schedule Proxy v10
Ключове виправлення: POST тіло кодується в Windows-1251, не UTF-8
"""

import re
import requests
from datetime import date, timedelta
from urllib.parse import urlencode, quote
from flask import Flask, jsonify, request
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

UKD_FORM = "http://195.162.83.28/cgi-bin/timetable.cgi?n=700"

LESSON_TIMES = {
    1: {"start": "08:30", "end": "09:50"},
    2: {"start": "10:00", "end": "11:20"},
    3: {"start": "11:30", "end": "12:50"},
    4: {"start": "13:30", "end": "14:50"},
    5: {"start": "15:00", "end": "16:20"},
    6: {"start": "16:30", "end": "17:50"},
    7: {"start": "18:00", "end": "19:20"},
}
DAY_MAP = {
    "понеділок": 0, "вівторок": 1, "середа": 2,
    "четвер": 3, "п'ятниця": 4, "п`ятниця": 4, "субота": 5,
}
DAYS_UA = ["Понеділок", "Вівторок", "Середа", "Четвер", "П'ятниця", "Субота"]

HEADERS = {
    "Content-Type": "application/x-www-form-urlencoded",
    "User-Agent":   "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer":      UKD_FORM,
    "Origin":       "http://195.162.83.28",
    "Accept":       "text/html,application/xhtml+xml,*/*;q=0.9",
    "Accept-Language": "uk-UA,uk;q=0.9,ru;q=0.8",
}


def encode_cp1251_body(fields: dict) -> bytes:
    """Кодує поля форми в Windows-1251 (як це робить браузер на сторінці з charset=windows-1251)."""
    parts = []
    for key, value in fields.items():
        k = quote(str(key).encode("windows-1251"), safe="")
        v = quote(str(value).encode("windows-1251"), safe="")
        parts.append(f"{k}={v}")
    return "&".join(parts).encode("ascii")


def decode_resp(resp) -> str:
    for enc in ("windows-1251", "utf-8", "koi8-u"):
        try:
            resp.encoding = enc
            t = resp.text
            if any(c in t for c in "АБВГДЕЄЖЗІЇаб"):
                return t
        except Exception:
            pass
    resp.encoding = "windows-1251"
    return resp.text


def fetch_schedule(group: str, d_from: str, d_to: str) -> str | None:
    session = requests.Session()
    # Спочатку відвідуємо сторінку — отримуємо cookies
    try:
        session.get(UKD_FORM, headers=HEADERS, timeout=10)
    except Exception:
        pass

    fields = {
        "faculty": "0",
        "teacher": "",
        "course":  "0",
        "group":   group,
        "sdate":   d_from,
        "edate":   d_to,
    }

    body = encode_cp1251_body(fields)

    try:
        resp = session.post(
            UKD_FORM,
            data=body,
            headers=HEADERS,
            timeout=15,
        )
        html = decode_resp(resp)
        has = "Розклад групи" in html
        print(f"POST cp1251 status={resp.status_code} len={len(html)} has_rozklad={has}")
        return html
    except Exception as e:
        print(f"POST error: {e}")
        return None


def get_week_dates(offset: int = 0):
    today = date.today()
    mon = today - timedelta(days=today.weekday()) + timedelta(weeks=offset)
    sun = mon + timedelta(days=6)
    return mon.strftime("%d.%m.%Y"), sun.strftime("%d.%m.%Y")


def strip_tags(html: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", html, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    for ent, ch in [("&nbsp;"," "),("&amp;","&"),("&lt;","<"),("&gt;",">")]:
        text = text.replace(ent, ch)
    text = re.sub(r"\s{2,}", " ", text)
    return text.strip()


def detect_type(text: str) -> str:
    t = text.lower()
    if "лаб" in t:                               return "Лабораторна"
    if "практ" in t or "пр." in t or "пз" in t: return "Практика"
    if "сем" in t:                               return "Семінар"
    return "Лекція"


def parse_schedule_html(html: str) -> list:
    lessons = []
    day_pat = re.compile(r'(\d{2}\.\d{2}\.\d{4})\s+([А-ЯҐЄІЇа-яґєіїʼ\'`]+)', re.IGNORECASE)
    sections = []
    for m in day_pat.finditer(html):
        day_raw = m.group(2).lower().strip().replace("`", "'")
        idx = DAY_MAP.get(day_raw)
        if idx is not None:
            sections.append({"pos": m.start(), "date": m.group(1),
                             "day_idx": idx, "day_name": DAYS_UA[idx]})

    for i, sec in enumerate(sections):
        chunk = html[sec["pos"]: sections[i+1]["pos"] if i+1 < len(sections) else len(html)]
        for row in re.findall(r'<tr[^>]*>(.*?)</tr>', chunk, re.DOTALL | re.IGNORECASE):
            cells = re.findall(r'<td[^>]*>(.*?)</td>', row, re.DOTALL | re.IGNORECASE)
            if len(cells) < 2:
                continue
            m2 = re.match(r'^(\d)$', strip_tags(cells[0]).strip())
            if not m2:
                continue
            pn = int(m2.group(1))
            if not 1 <= pn <= 7:
                continue
            content = strip_tags(" ".join(cells[2:] if len(cells) > 2 else cells[1:])).strip()
            if not content:
                continue
            lines = [l.strip() for l in re.split(r'\n+', content) if l.strip()]
            subject = lines[0] if lines else content
            teacher = lines[1] if len(lines) > 1 else ""
            room = ""
            rm = re.search(r'ауд\.?\s*(\S+)', content, re.IGNORECASE)
            if rm:
                room = "ауд." + rm.group(1)
                subject = re.sub(r'\s*ауд\.?\s*\S+', '', subject).strip()
                teacher = re.sub(r'\s*ауд\.?\s*\S+', '', teacher).strip()
            elif len(lines) > 2:
                room = lines[-1]
            t = LESSON_TIMES.get(pn, {"start": "??:??", "end": "??:??"})
            lessons.append({
                "pairNumber": pn, "dayOfWeek": sec["day_idx"],
                "dayName": sec["day_name"], "date": sec["date"],
                "subject": subject, "teacher": teacher, "room": room,
                "type": detect_type(subject),
                "timeStart": t["start"], "timeEnd": t["end"],
            })
    return lessons


# ── Routes ─────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return jsonify({"service": "UKD Schedule Proxy", "version": "10.0"})


@app.route("/api/schedule")
def get_schedule():
    group = request.args.get("group", "").strip()
    if not group:
        return jsonify({"error": "Вкажіть ?group=КІПЗс-24-3"}), 400
    d_from, d_to = get_week_dates(int(request.args.get("week", 0)))

    html = fetch_schedule(group, d_from, d_to)
    if not html:
        return jsonify({"error": "УКД недоступний"}), 502
    if "Розклад групи" not in html:
        return jsonify({"error": f"Групу '{group}' не знайдено", "lessons": [], "count": 0}), 404

    lessons = parse_schedule_html(html)
    return jsonify({"group": group, "week": {"from": d_from, "to": d_to},
                    "lessons": lessons, "count": len(lessons)})


@app.route("/api/health")
def health():
    try:
        r = requests.get(UKD_FORM, timeout=10)
        ok = r.status_code == 200
    except Exception:
        ok = False
    return jsonify({"proxy": "ok", "ukd_server": "ok" if ok else "unreachable"})


@app.route("/api/debug")
def debug():
    group = request.args.get("group", "КІПЗс-24-3").strip()
    d_from, d_to = get_week_dates(0)

    # Показуємо як кодується група
    cp1251_body = encode_cp1251_body({
        "faculty": "0", "teacher": "", "course": "0",
        "group": group, "sdate": d_from, "edate": d_to,
    })

    html = fetch_schedule(group, d_from, d_to)
    if not html:
        return jsonify({"error": "УКД недоступний"}), 502

    idx = html.find("Розклад групи")
    lessons = parse_schedule_html(html) if idx >= 0 else []

    day_pat = re.compile(r'(\d{2}\.\d{2}\.\d{4})\s+([А-ЯҐЄІЇа-яґєіїʼ\'`]+)', re.IGNORECASE)

    return jsonify({
        "group":             group,
        "week":              {"from": d_from, "to": d_to},
        "cp1251_body_sent":  cp1251_body.decode("ascii"),
        "html_length":       len(html),
        "has_rozklad_grupy": idx >= 0,
        "days_found":        [{"date": m.group(1), "day": m.group(2)} for m in day_pat.finditer(html)],
        "lessons_parsed":    lessons,
        "html_after_rozklad": html[idx:idx+2000] if idx >= 0 else html[3500:5500],
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=False)
