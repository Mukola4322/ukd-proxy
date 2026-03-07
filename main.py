"""
UKD Schedule Proxy Server v6
Форма приймає назву групи текстом + дати. Повертає розклад по тижню.
"""

import re
import requests
from datetime import date, timedelta
from flask import Flask, jsonify, request
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

UKD_BASE = "http://195.162.83.28/cgi-bin/timetable.cgi"

LESSON_TIMES = {
    1: {"start": "08:30", "end": "09:50"},
    2: {"start": "10:00", "end": "11:20"},
    3: {"start": "11:30", "end": "12:50"},
    4: {"start": "13:30", "end": "14:50"},
    5: {"start": "15:00", "end": "16:20"},
    6: {"start": "16:30", "end": "17:50"},
    7: {"start": "18:00", "end": "19:20"},
}

# Відповідність українських назв днів до індексу 0=Пн
DAY_MAP = {
    "понеділок": 0, "вівторок": 1, "середа": 2,
    "четвер": 3,    "п'ятниця": 4, "субота": 5,
}
DAYS_UA = ["Понеділок", "Вівторок", "Середа", "Четвер", "П'ятниця", "Субота"]

HEADERS = {
    "Content-Type": "application/x-www-form-urlencoded",
    "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)",
    "Referer": UKD_BASE,
}


def decode_html(resp) -> str:
    for enc in ("windows-1251", "utf-8", "koi8-u"):
        try:
            resp.encoding = enc
            text = resp.text
            if any(c in text for c in "АБВГДЕЄЖЗІЇаб"):
                return text
        except Exception:
            continue
    resp.encoding = "windows-1251"
    return resp.text


def fetch_schedule(group: str, date_from: str, date_to: str) -> str | None:
    """POST-запит з назвою групи і датами."""
    data = {
        "n":        "700",
        "teacher":  "",
        "group":    group,
        "sdate":    date_from,   # формат: дд.мм.рррр
        "edate":    date_to,
        "setVedP":  "1",
    }
    try:
        resp = requests.post(UKD_BASE, data=data, headers=HEADERS, timeout=15)
        return decode_html(resp)
    except Exception as e:
        print(f"POST error: {e}")
        return None


def strip_tags(html: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", html, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    for ent, ch in [("&nbsp;"," "),("&amp;","&"),("&lt;","<"),("&gt;",">"),("&quot;",'"')]:
        text = text.replace(ent, ch)
    text = re.sub(r"&#\d+;", "", text)
    text = re.sub(r"\s{2,}", " ", text)
    return text.strip()


def detect_type(text: str) -> str:
    t = text.lower()
    if "лаб" in t:                               return "Лабораторна"
    if "практ" in t or "пр." in t or "пз" in t: return "Практика"
    if "сем" in t:                               return "Семінар"
    if "конс" in t:                              return "Консультація"
    return "Лекція"


def parse_schedule_html(html: str) -> list[dict]:
    """
    Сторінка розкладу УКД показує тиждень колонками:
    Дата+День | пара1 | пара2 | ...
    Або: блоки по кожному дню з таблицею пар.
    
    Реальна структура (з скріна): 
    - Заголовок дня: "09.03.2026 Понеділок"
    - Таблиця: номер | час | предмет/викладач/аудиторія
    """
    lessons = []

    # Шукаємо блоки днів: дата + назва дня
    # Розбиваємо HTML на секції по датах
    # Патерн: "дд.мм.рррр ДеньТижня"
    day_pattern = re.compile(
        r'(\d{2}\.\d{2}\.\d{4})\s+([А-ЯҐЄІЇа-яґєіїʼ\']+)',
        re.IGNORECASE
    )

    # Знаходимо всі таблиці з розкладом
    # Структура зі скріна: <table> з рядками: номерПари | час | вміст
    tables = re.findall(r'<table[^>]*>(.*?)</table>', html, re.DOTALL | re.IGNORECASE)

    # Знаходимо дні та їх позиції в HTML
    day_sections = []
    for m in day_pattern.finditer(html):
        day_name = m.group(2).lower().strip()
        day_idx = DAY_MAP.get(day_name)
        if day_idx is not None:
            day_sections.append({
                "pos":     m.start(),
                "date":    m.group(1),
                "day_idx": day_idx,
                "day_name": DAYS_UA[day_idx],
            })

    if not day_sections:
        return []

    # Для кожного дня знаходимо наступну таблицю з парами
    for i, section in enumerate(day_sections):
        # Беремо HTML від поточного дня до наступного
        start = section["pos"]
        end = day_sections[i + 1]["pos"] if i + 1 < len(day_sections) else len(html)
        day_html = html[start:end]

        # Шукаємо рядки таблиці в цьому блоці
        rows = re.findall(r'<tr[^>]*>(.*?)</tr>', day_html, re.DOTALL | re.IGNORECASE)
        for row in rows:
            cells = re.findall(r'<td[^>]*>(.*?)</td>', row, re.DOTALL | re.IGNORECASE)
            if len(cells) < 2:
                continue

            # Перша клітинка — номер пари (ціле число)
            first = strip_tags(cells[0]).strip()
            m2 = re.match(r'^(\d)$', first)
            if not m2:
                continue
            pair_num = int(m2.group(1))
            if pair_num < 1 or pair_num > 7:
                continue

            # Час (2-га клітинка, може бути "08:30\n09:50")
            # Вміст (3-тя клітинка і далі — або об'єднана)
            content_cells = cells[2:] if len(cells) > 2 else cells[1:]
            content_html = " ".join(content_cells)
            content = strip_tags(content_html).strip()

            if not content:
                continue

            # Парсимо вміст: предмет, викладач, аудиторія
            lines = [l.strip() for l in re.split(r'\n+', content) if l.strip()]

            subject     = lines[0] if len(lines) > 0 else content
            teacher     = lines[1] if len(lines) > 1 else ""
            room        = ""

            # Шукаємо аудиторію — зазвичай останній рядок або "ауд.XXX"
            room_match = re.search(r'ауд\.?\s*(\S+)', content, re.IGNORECASE)
            if room_match:
                room = "ауд." + room_match.group(1)
                # Прибираємо аудиторію з кінця subject/teacher
                subject = re.sub(r'ауд\.?\s*\S+', '', subject).strip()
                teacher = re.sub(r'ауд\.?\s*\S+', '', teacher).strip()
            elif len(lines) > 2:
                room = lines[-1]

            # Прибираємо назви інших груп з предмету (через кому перед прізвищем)
            # Формат: "Предмет (Л) група1, група2, ... Прізвище ауд"
            lesson_type = detect_type(subject)

            times = LESSON_TIMES.get(pair_num, {"start": "??:??", "end": "??:??"})

            lessons.append({
                "pairNumber": pair_num,
                "dayOfWeek":  section["day_idx"],
                "dayName":    section["day_name"],
                "date":       section["date"],
                "subject":    subject,
                "teacher":    teacher,
                "room":       room,
                "type":       lesson_type,
                "timeStart":  times["start"],
                "timeEnd":    times["end"],
            })

    return lessons


def get_week_dates(offset_weeks: int = 0):
    """Повертає (пн, нд) поточного або зміщеного тижня у форматі дд.мм.рррр."""
    today = date.today()
    monday = today - timedelta(days=today.weekday()) + timedelta(weeks=offset_weeks)
    sunday = monday + timedelta(days=6)
    return monday.strftime("%d.%m.%Y"), sunday.strftime("%d.%m.%Y")


# ── Routes ─────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return jsonify({"service": "UKD Schedule Proxy", "version": "6.0"})


@app.route("/api/schedule")
def get_schedule():
    group = request.args.get("group", "").strip()
    if not group:
        return jsonify({"error": "Вкажіть ?group=КІПЗс-24-3"}), 400

    # Можна вказати зміщення тижня: ?week=0 (поточний), ?week=1 (наступний)
    week_offset = int(request.args.get("week", 0))
    date_from, date_to = get_week_dates(week_offset)

    html = fetch_schedule(group, date_from, date_to)
    if not html:
        return jsonify({"error": "Не вдалося підключитись до УКД"}), 502

    # Перевіряємо чи група знайдена
    if "Розклад групи" not in html and group not in html:
        return jsonify({
            "error": f"Групу '{group}' не знайдено або розклад порожній",
            "group": group, "lessons": [], "count": 0,
            "week": {"from": date_from, "to": date_to}
        }), 404

    lessons = parse_schedule_html(html)
    return jsonify({
        "group":   group,
        "week":    {"from": date_from, "to": date_to},
        "lessons": lessons,
        "count":   len(lessons),
    })


@app.route("/api/health")
def health():
    d_from, d_to = get_week_dates()
    try:
        resp = requests.get(UKD_BASE, params={"n": "700"}, timeout=10)
        ukd_ok = resp.status_code == 200
    except Exception:
        ukd_ok = False
    return jsonify({"proxy": "ok", "ukd_server": "ok" if ukd_ok else "unreachable"})


@app.route("/api/debug")
def debug():
    group = request.args.get("group", "КІПЗс-24-3").strip()
    d_from, d_to = get_week_dates()

    html = fetch_schedule(group, d_from, d_to)
    if not html:
        return jsonify({"error": "УКД недоступний"}), 502

    lessons = parse_schedule_html(html)

    # Знаходимо дні в HTML
    day_pattern = re.compile(r'(\d{2}\.\d{2}\.\d{4})\s+([А-ЯҐЄІЇа-яґєіїʼ\']+)', re.IGNORECASE)
    days_found = [{"date": m.group(1), "day": m.group(2)} for m in day_pattern.finditer(html)]

    # Перші рядки таблиць
    rows = re.findall(r'<tr[^>]*>(.*?)</tr>', html, re.DOTALL | re.IGNORECASE)
    sample_rows = []
    for i, row in enumerate(rows[:20]):
        cells = re.findall(r'<td[^>]*>(.*?)</td>', row, re.DOTALL | re.IGNORECASE)
        if cells:
            sample_rows.append({
                "row": i,
                "cells": [strip_tags(c)[:100] for c in cells[:5]]
            })

    return jsonify({
        "group":       group,
        "week":        {"from": d_from, "to": d_to},
        "html_length": len(html),
        "days_found":  days_found,
        "table_rows_sample": sample_rows,
        "lessons_parsed": lessons,
        "html_preview": html[html.find("Розклад групи"):html.find("Розклад групи")+3000] if "Розклад групи" in html else html[:3000],
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=False)
