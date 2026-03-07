"""
UKD Schedule Proxy Server v7
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

DAY_MAP = {
    "понеділок": 0, "вівторок": 1, "середа": 2,
    "четвер": 3, "п'ятниця": 4, "п`ятниця": 4, "пятниця": 4, "субота": 5,
}
DAYS_UA = ["Понеділок", "Вівторок", "Середа", "Четвер", "П'ятниця", "Субота"]

HEADERS = {
    "Content-Type": "application/x-www-form-urlencoded",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": UKD_BASE + "?n=700",
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


def get_main_page() -> str | None:
    try:
        resp = requests.get(UKD_BASE, params={"n": "700"}, headers=HEADERS, timeout=15)
        return decode_html(resp)
    except Exception as e:
        print(f"GET error: {e}")
        return None


def post_form(data: dict) -> str | None:
    try:
        resp = requests.post(
            UKD_BASE + "?n=700",
            data=data,
            headers=HEADERS,
            timeout=15
        )
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


def get_week_dates(offset: int = 0):
    today = date.today()
    mon = today - timedelta(days=today.weekday()) + timedelta(weeks=offset)
    sun = mon + timedelta(days=6)
    return mon.strftime("%d.%m.%Y"), sun.strftime("%d.%m.%Y")


def parse_schedule_html(html: str) -> list[dict]:
    lessons = []

    # Шукаємо блоки виду: "09.03.2026 Понеділок"
    day_pattern = re.compile(
        r'(\d{2}\.\d{2}\.\d{4})\s+([А-ЯҐЄІЇа-яґєіїʼ\'\`]+)',
        re.IGNORECASE | re.UNICODE
    )

    day_sections = []
    for m in day_pattern.finditer(html):
        day_raw = m.group(2).lower().strip().replace("`", "'")
        day_idx = DAY_MAP.get(day_raw)
        if day_idx is not None:
            day_sections.append({
                "pos": m.start(), "date": m.group(1),
                "day_idx": day_idx, "day_name": DAYS_UA[day_idx],
            })

    for i, section in enumerate(day_sections):
        start = section["pos"]
        end = day_sections[i + 1]["pos"] if i + 1 < len(day_sections) else len(html)
        day_html = html[start:end]

        rows = re.findall(r'<tr[^>]*>(.*?)</tr>', day_html, re.DOTALL | re.IGNORECASE)
        for row in rows:
            cells = re.findall(r'<td[^>]*>(.*?)</td>', row, re.DOTALL | re.IGNORECASE)
            if len(cells) < 2:
                continue
            first = strip_tags(cells[0]).strip()
            m2 = re.match(r'^(\d)$', first)
            if not m2:
                continue
            pair_num = int(m2.group(1))
            if not 1 <= pair_num <= 7:
                continue

            content_html = " ".join(cells[2:] if len(cells) > 2 else cells[1:])
            content = strip_tags(content_html).strip()
            if not content:
                continue

            lines = [l.strip() for l in re.split(r'\n+', content) if l.strip()]
            subject = lines[0] if lines else content
            teacher = lines[1] if len(lines) > 1 else ""
            room = ""

            room_m = re.search(r'ауд\.?\s*(\S+)', content, re.IGNORECASE)
            if room_m:
                room = "ауд." + room_m.group(1)
                subject = re.sub(r'\s*ауд\.?\s*\S+', '', subject).strip()
                teacher = re.sub(r'\s*ауд\.?\s*\S+', '', teacher).strip()
            elif len(lines) > 2:
                room = lines[-1]

            times = LESSON_TIMES.get(pair_num, {"start": "??:??", "end": "??:??"})
            lessons.append({
                "pairNumber": pair_num,
                "dayOfWeek":  section["day_idx"],
                "dayName":    section["day_name"],
                "date":       section["date"],
                "subject":    subject,
                "teacher":    teacher,
                "room":       room,
                "type":       detect_type(subject),
                "timeStart":  times["start"],
                "timeEnd":    times["end"],
            })

    return lessons


# ── Routes ─────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return jsonify({"service": "UKD Schedule Proxy", "version": "7.0"})


@app.route("/api/schedule")
def get_schedule():
    group = request.args.get("group", "").strip()
    if not group:
        return jsonify({"error": "Вкажіть ?group=КІПЗс-24-3"}), 400

    week = int(request.args.get("week", 0))
    d_from, d_to = get_week_dates(week)

    html = post_form({
        "faculty": "0",
        "teacher": "",
        "group":   group,
        "course":  "0",
        "sdate":   d_from,
        "edate":   d_to,
    })
    if not html:
        return jsonify({"error": "Не вдалося підключитись до УКД"}), 502

    if "Розклад групи" not in html:
        return jsonify({
            "error": f"Групу '{group}' не знайдено або розклад порожній",
            "group": group, "lessons": [], "count": 0,
        }), 404

    lessons = parse_schedule_html(html)
    return jsonify({
        "group": group, "week": {"from": d_from, "to": d_to},
        "lessons": lessons, "count": len(lessons),
    })


@app.route("/api/health")
def health():
    html = get_main_page()
    return jsonify({"proxy": "ok", "ukd_server": "ok" if html else "unreachable"})


@app.route("/api/debug")
def debug():
    group = request.args.get("group", "КІПЗс-24-3").strip()
    d_from, d_to = get_week_dates(0)

    # Крок 1: головна сторінка — витягуємо всі поля форми
    main_html = get_main_page()
    form_fields = []
    if main_html:
        # Знаходимо всі input/select/textarea з name атрибутом
        for tag in re.finditer(
            r'<(input|select|textarea)[^>]+name=["\']([^"\']+)["\'][^>]*>',
            main_html, re.IGNORECASE
        ):
            form_fields.append({
                "tag": tag.group(1),
                "name": tag.group(2),
                "full": tag.group(0)[:120]
            })

    # Крок 2: POST з групою
    html = post_form({
        "faculty": "0",
        "teacher": "",
        "group":   group,
        "course":  "0",
        "sdate":   d_from,
        "edate":   d_to,
    })

    result = {
        "week": {"from": d_from, "to": d_to},
        "form_fields_on_main_page": form_fields,
        "post_html_length": len(html) if html else 0,
        "has_rozklad_grupы": "Розклад групи" in html if html else False,
        "post_html_preview": html[:4000] if html else None,
    }

    if html:
        day_pattern = re.compile(
            r'(\d{2}\.\d{2}\.\d{4})\s+([А-ЯҐЄІЇа-яґєіїʼ\'\`]+)',
            re.IGNORECASE
        )
        result["days_found"] = [
            {"date": m.group(1), "day": m.group(2)}
            for m in day_pattern.finditer(html)
        ]

        rows = re.findall(r'<tr[^>]*>(.*?)</tr>', html, re.DOTALL | re.IGNORECASE)
        result["table_rows_sample"] = [
            {"row": i, "cells": [strip_tags(c)[:80] for c in
             re.findall(r'<td[^>]*>(.*?)</td>', row, re.DOTALL | re.IGNORECASE)[:5]]}
            for i, row in enumerate(rows[:15])
            if re.findall(r'<td[^>]*>(.*?)</td>', row, re.DOTALL | re.IGNORECASE)
        ]

        result["lessons_parsed"] = parse_schedule_html(html)

    return jsonify(result)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=False)
