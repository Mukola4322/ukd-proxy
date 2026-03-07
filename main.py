"""
UKD Schedule Proxy Server
"""

import re
import requests
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

DAYS = ["Понеділок", "Вівторок", "Середа", "Четвер", "П'ятниця", "Субота"]


def fetch_ukd(params: dict):
    try:
        resp = requests.get(UKD_BASE, params=params, timeout=15)
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
    except requests.RequestException as e:
        print(f"UKD fetch error: {e}")
        return None


def strip_tags(html: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", html, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"&lt;", "<", text)
    text = re.sub(r"&gt;", ">", text)
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


def parse_cell(cell_html: str, pair_num: int, day_idx: int):
    clean = strip_tags(cell_html)
    if not clean or not clean.replace(" ", "").replace("\n", ""):
        return None
    lines = [l.strip() for l in re.split(r"[\n]+", clean) if l.strip()]
    if not lines:
        return None
    subject     = lines[0] if len(lines) > 0 else ""
    teacher     = lines[1] if len(lines) > 1 else ""
    room        = lines[2] if len(lines) > 2 else ""
    lesson_type = detect_type(clean)
    if not room:
        m = re.search(r"([А-ЯҐЄІЇа-яґєії]-?\d+|\d{3,4})", clean)
        if m:
            room = m.group(1)
    times = LESSON_TIMES.get(pair_num, {"start": "??:??", "end": "??:??"})
    return {
        "pairNumber": pair_num,
        "dayOfWeek":  day_idx,
        "dayName":    DAYS[day_idx],
        "subject":    subject,
        "teacher":    teacher,
        "room":       room,
        "type":       lesson_type,
        "timeStart":  times["start"],
        "timeEnd":    times["end"],
    }


def parse_schedule_html(html: str) -> list:
    lessons = []
    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.DOTALL | re.IGNORECASE)
    for row in rows:
        cells = re.findall(r"<td[^>]*>(.*?)</td>", row, re.DOTALL | re.IGNORECASE)
        if not cells:
            continue
        first = strip_tags(cells[0]).strip()
        m = re.match(r"^(\d)$", first)
        if not m:
            continue
        pair_num = int(m.group(1))
        if pair_num < 1 or pair_num > 7:
            continue
        for day_idx, cell_html in enumerate(cells[1:7]):
            lesson = parse_cell(cell_html, pair_num, day_idx)
            if lesson:
                lessons.append(lesson)
    return lessons


def parse_groups_html(html: str) -> list:
    groups = re.findall(
        r'<option[^>]+value=["\']?([^"\'>\s]+)["\']?[^>]*>',
        html, re.IGNORECASE
    )
    return sorted(set(g for g in groups if g and g != "0"))


@app.route("/")
def index():
    return jsonify({"service": "UKD Schedule Proxy", "version": "2.0"})


@app.route("/api/groups")
def get_groups():
    html = fetch_ukd({"n": "700"})
    if not html:
        return jsonify({"error": "Не вдалося підключитись до сервера УКД"}), 502
    groups = parse_groups_html(html)
    return jsonify({"groups": groups, "count": len(groups)})


@app.route("/api/schedule")
def get_schedule():
    group = request.args.get("group", "").strip()
    if not group:
        return jsonify({"error": "Вкажіть ?group=НазваГрупи"}), 400
    html = fetch_ukd({"n": "700", "grp": group})
    if not html:
        return jsonify({"error": "Не вдалося підключитись до сервера УКД"}), 502
    lessons = parse_schedule_html(html)
    return jsonify({"group": group, "lessons": lessons, "count": len(lessons)})


@app.route("/api/health")
def health():
    ukd_ok = fetch_ukd({"n": "700"}) is not None
    return jsonify({"proxy": "ok", "ukd_server": "ok" if ukd_ok else "unreachable"})


@app.route("/api/debug")
def debug():
    """Показує сирий HTML і розпарсені рядки — для діагностики."""
    group = request.args.get("group", "").strip()
    params = {"n": "700", "grp": group} if group else {"n": "700"}
    html = fetch_ukd(params)
    if not html:
        return jsonify({"error": "УКД недоступний"}), 502

    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.DOTALL | re.IGNORECASE)
    parsed_rows = []
    for i, row in enumerate(rows[:20]):
        cells = re.findall(r"<td[^>]*>(.*?)</td>", row, re.DOTALL | re.IGNORECASE)
        parsed_rows.append({
            "row_index": i,
            "cells_count": len(cells),
            "cells": [strip_tags(c)[:120] for c in cells[:8]]
        })

    return jsonify({
        "html_length": len(html),
        "html_preview": html[:3000],
        "total_rows_found": len(rows),
        "first_20_rows": parsed_rows,
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=False)
