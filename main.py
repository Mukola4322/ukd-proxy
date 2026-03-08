"""
UKD Schedule Proxy v11
Структура HTML: <h4>дд.мм.рррр <small>понеділок</small></h4>
               <table><tr><td>1</td><td>08:30<br>09:50</td><td>вміст</td></tr>
"""

import re
import requests
from datetime import date, timedelta
from urllib.parse import quote
from flask import Flask, jsonify, request
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

UKD_FORM = "http://195.162.83.28/cgi-bin/timetable.cgi?n=700"

LESSON_TIMES = {
    1: {"start": "08:30", "end": "09:50"},
    2: {"start": "10:00", "end": "11:20"},
    3: {"start": "12:00", "end": "13:20"},
    4: {"start": "13:30", "end": "14:50"},
    5: {"start": "15:10", "end": "16:30"},
    6: {"start": "16:40", "end": "18:00"},
    7: {"start": "18:10", "end": "19:30"},
}
DAY_MAP = {
    "понеділок": 0, "вівторок": 1, "середа": 2,
    "четвер": 3, "п'ятниця": 4, "п`ятниця": 4, "пятниця": 4, "субота": 5,
}
DAYS_UA = ["Понеділок", "Вівторок", "Середа", "Четвер", "П'ятниця", "Субота"]

HEADERS = {
    "Content-Type":  "application/x-www-form-urlencoded",
    "User-Agent":    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer":       UKD_FORM,
    "Origin":        "http://195.162.83.28",
    "Accept":        "text/html,application/xhtml+xml,*/*;q=0.9",
    "Accept-Language": "uk-UA,uk;q=0.9",
}


def encode_cp1251(fields: dict) -> bytes:
    parts = []
    for k, v in fields.items():
        parts.append(
            quote(str(k).encode("windows-1251"), safe="") + "=" +
            quote(str(v).encode("windows-1251"), safe="")
        )
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
    try:
        session.get(UKD_FORM, headers=HEADERS, timeout=10)
    except Exception:
        pass
    try:
        resp = session.post(
            UKD_FORM,
            data=encode_cp1251({"faculty":"0","teacher":"","course":"0",
                                 "group":group,"sdate":d_from,"edate":d_to}),
            headers=HEADERS, timeout=15,
        )
        return decode_resp(resp)
    except Exception as e:
        print(f"POST error: {e}")
        return None


def get_week_dates(offset: int = 0):
    today = date.today()
    # В Україні тиждень Пн-Нд. Якщо сьогодні неділя (weekday=6) — це кінець тижня,
    # тому "поточний" тиждень = той що починається наступного понеділка
    days_since_monday = today.weekday()  # 0=Пн, 6=Нд
    mon = today - timedelta(days=days_since_monday) + timedelta(weeks=offset)
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


def parse_schedule_html(html: str, group_name: str = "") -> list:
    lessons = []

    block_pattern = re.compile(
        r'<h4>\s*(\d{2}\.\d{2}\.\d{4})\s*<small>\s*([^<]+?)\s*</small>.*?</h4>'
        r'(.*?)'
        r'(?=<h4>\s*\d{2}\.\d{2}\.\d{4}|$)',
        re.DOTALL | re.IGNORECASE
    )

    for block in block_pattern.finditer(html):
        date_str = block.group(1)
        day_raw  = block.group(2).strip().lower().replace("`","'")
        day_idx  = DAY_MAP.get(day_raw)
        if day_idx is None:
            continue

        block_html = block.group(3)

        # Беремо тільки першу таблицю в блоці дня
        table_m = re.search(r'<table[^>]*>(.*?)</table>', block_html, re.DOTALL | re.IGNORECASE)
        if not table_m:
            continue
        table_html = table_m.group(1)
        rows = re.findall(r'<tr[^>]*>(.*?)</tr>', table_html, re.DOTALL | re.IGNORECASE)

        for row in rows:
            cells = re.findall(r'<td[^>]*>(.*?)</td>', row, re.DOTALL | re.IGNORECASE)
            if len(cells) < 3:
                continue

            pair_text = strip_tags(cells[0]).strip()
            m = re.match(r'^(\d)$', pair_text)
            if not m:
                continue
            pn = int(m.group(1))
            if not 1 <= pn <= 7:
                continue

            content = strip_tags(" ".join(cells[2:])).strip()
            if not content:
                continue

            # Фільтр потокових пар: якщо клітинка містить перелік кількох груп —
            # показуємо пару тільки якщо наша група згадується
            if group_name:
                # Знаходимо всі згадки груп (КІПЗс-24-3, КДс-24-1, ІТ-21 тощо)
                group_mentions = re.findall(
                    r'[А-ЯҐЄІЇа-яґєії]{1,8}(?:с|з|ь)?-\d{2}(?:-\d)?',
                    content
                )
                if len(group_mentions) >= 2:
                    # Є список груп — перевіряємо чи наша є серед них
                    our_group_found = any(
                        group_name.lower() == g.lower() or
                        group_name.lower() in g.lower()
                        for g in group_mentions
                    )
                    if not our_group_found:
                        continue

            lines = [l.strip() for l in re.split(r'\n+', content) if l.strip()]
            raw_subject = lines[0] if lines else content

            # Прибираємо тип заняття з назви: (Пр), (Л), (Сем), (Лаб)
            raw_subject = re.sub(r'\s*\([ЛПСлпс][^)]{0,8}\)', '', raw_subject).strip()

            # Шукаємо аудиторію
            room = ""
            room_m = re.search(r'ауд\.?\s*([А-ЯҐЄІЇа-яґєії]?-?\d+[/\w]*)', content, re.IGNORECASE)
            if room_m:
                room = "ауд." + room_m.group(1)

            # Шукаємо викладача — Прізвище Ім'я Побатькові у будь-якому рядку
            teacher = ""
            full_text = " ".join(lines)
            teacher_m = re.search(
                r'([А-ЯҐЄІЇ][а-яґєіїʼ\']+\s+[А-ЯҐЄІЇ][а-яґєіїʼ\']+\s+[А-ЯҐЄІЇ][а-яґєіїʼ\']+(?:на|ич|ів|вич)?)',
                full_text
            )
            if teacher_m:
                teacher = teacher_m.group(1).strip()
                # Прибираємо аудиторію з викладача якщо є
                teacher = re.sub(r'\s*ауд\.?\s*\S+', '', teacher).strip()

            # Назва предмету = перший рядок БЕЗ викладача і без аудиторії
            subject = raw_subject
            if teacher:
                subject = subject.replace(teacher, '').strip()
            subject = re.sub(r'\s*ауд\.?\s*[А-ЯҐЄІЇа-яґєії]?-?\d+[/\w]*', '', subject).strip()
            subject = re.sub(r'\s{2,}', ' ', subject).strip()

            if not subject:
                continue

            t = LESSON_TIMES.get(pn, {"start":"??:??","end":"??:??"})
            lessons.append({
                "pairNumber": pn,
                "dayOfWeek":  day_idx,
                "dayName":    DAYS_UA[day_idx],
                "date":       date_str,
                "subject":    subject,
                "teacher":    teacher,
                "room":       room,
                "type":       detect_type(content),
                "timeStart":  t["start"],
                "timeEnd":    t["end"],
            })

    return lessons

# ── Routes ─────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return jsonify({"service": "UKD Schedule Proxy", "version": "11.0"})


@app.route("/api/schedule")
def get_schedule():
    group = request.args.get("group", "").strip()
    if not group:
        return jsonify({"error": "Вкажіть ?group=КІПЗс-24-3"}), 400

    # Клієнт може передати конкретні дати (надійніше ніж рахувати на сервері)
    sdate = request.args.get("sdate", "").strip()
    edate = request.args.get("edate", "").strip()
    if not sdate or not edate:
        week = int(request.args.get("week", 0))
        sdate, edate = get_week_dates(week)

    html = fetch_schedule(group, sdate, edate)
    if not html:
        return jsonify({"error": "УКД недоступний"}), 502
    if "Розклад групи" not in html:
        return jsonify({"error": f"Групу '{group}' не знайдено",
                        "lessons": [], "count": 0}), 404

    lessons = parse_schedule_html(html, group_name=group)
    return jsonify({"group": group, "week": {"from": sdate, "to": edate},
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

    html = fetch_schedule(group, d_from, d_to)
    if not html:
        return jsonify({"error": "УКД недоступний"}), 502

    idx = html.find("Розклад групи")
    lessons = parse_schedule_html(html, group_name=group)

    # Знаходимо h4 блоки для діагностики
    h4_blocks = re.findall(r'<h4>[^<]*\d{2}\.\d{2}\.\d{4}.*?</h4>', html, re.IGNORECASE)

    return jsonify({
        "group":             group,
        "week":              {"from": d_from, "to": d_to},
        "has_rozklad_grupy": idx >= 0,
        "h4_day_blocks":     [strip_tags(b) for b in h4_blocks[:10]],
        "lessons_count":     len(lessons),
        "lessons_parsed":    lessons,
        "html_sample":       html[idx:idx+2000] if idx >= 0 else "",
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=False)
