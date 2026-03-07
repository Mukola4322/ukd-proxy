"""
UKD Schedule Proxy Server
Підключається до внутрішнього сервера УКД і віддає JSON твоєму iPhone-додатку.
Деплой: Railway / Render (безкоштовно)
"""

import re
import json
import requests
from flask import Flask, jsonify, request
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

UKD_BASE = "http://195.162.83.28/cgi-bin/timetable.cgi"

# Часи пар УКД
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


def fetch_ukd(params: dict) -> str | None:
    """Завантажує HTML з сервера УКД з правильним кодуванням."""
    try:
        resp = requests.get(UKD_BASE, params=params, timeout=10)
        # Сервер УКД повертає Windows-1251
        resp.encoding = "windows-1251"
        return resp.text
    except requests.RequestException as e:
        print(f"UKD fetch error: {e}")
        return None


def strip_tags(html: str) -> str:
    """Прибирає HTML-теги і нормалізує пробіли."""
    text = re.sub(r"<br\s*/?>", "\n", html, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"&lt;", "<", text)
    text = re.sub(r"&gt;", ">", text)
    text = re.sub(r"&#\d+;", "", text)
    return text.strip()


def detect_type(text: str) -> str:
    t = text.lower()
    if "лаб" in t:
        return "Лабораторна"
    if "практ" in t or "пр." in t or "пз" in t:
        return "Практика"
    if "сем" in t:
        return "Семінар"
    if "конс" in t:
        return "Консультація"
    return "Лекція"


def parse_cell(cell_html: str, pair_num: int, day_idx: int) -> dict | None:
    """Парсить одну клітинку розкладу."""
    clean = strip_tags(cell_html)
    # Якщо клітинка порожня — пропускаємо
    if not clean or clean.replace(" ", "").replace("\n", "") == "":
        return None

    lines = [l.strip() for l in re.split(r"[\n/|]+", clean) if l.strip()]
    if not lines:
        return None

    subject = lines[0] if len(lines) > 0 else ""
    teacher  = lines[1] if len(lines) > 1 else ""
    room     = lines[2] if len(lines) > 2 else ""
    lesson_type = detect_type(clean)

    # Витягуємо аудиторію з тексту якщо не знайдено
    if not room:
        room_match = re.search(r"([А-ЯҐЄІЇа-яґєії]-?\d+|\d{3})", clean)
        if room_match:
            room = room_match.group(1)

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


def parse_schedule_html(html: str) -> list[dict]:
    """Парсить HTML-таблицю розкладу і повертає список занять."""
    lessons = []

    # Знаходимо всі рядки таблиці
    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.DOTALL | re.IGNORECASE)

    for row in rows:
        cells = re.findall(r"<td[^>]*>(.*?)</td>", row, re.DOTALL | re.IGNORECASE)
        if not cells:
            continue

        # Перша клітинка — номер пари
        first = strip_tags(cells[0]).strip()
        pair_match = re.match(r"^(\d)$", first)
        if not pair_match:
            continue

        pair_num = int(pair_match.group(1))
        if pair_num < 1 or pair_num > 7:
            continue

        # Клітинки 1–6: дні Пн–Сб
        for day_idx, cell_html in enumerate(cells[1:7]):
            lesson = parse_cell(cell_html, pair_num, day_idx)
            if lesson:
                lessons.append(lesson)

    return lessons


def parse_groups_html(html: str) -> list[str]:
    """Витягує список груп зі select-списку."""
    groups = re.findall(
        r'<option[^>]+value=["\']?([^"\'>\s]+)["\']?[^>]*>',
        html, re.IGNORECASE
    )
    return sorted(set(g for g in groups if g and g != "0"))


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return jsonify({
        "service": "UKD Schedule Proxy",
        "version": "1.0",
        "endpoints": {
            "groups":   "/api/groups",
            "schedule": "/api/schedule?group=ІТ-21",
        }
    })


@app.route("/api/groups")
def get_groups():
    """Повертає список усіх груп."""
    html = fetch_ukd({"n": "700"})
    if not html:
        return jsonify({"error": "Не вдалося підключитись до сервера УКД"}), 502

    groups = parse_groups_html(html)
    return jsonify({"groups": groups, "count": len(groups)})


@app.route("/api/schedule")
def get_schedule():
    """Повертає розклад для вказаної групи."""
    group = request.args.get("group", "").strip()
    if not group:
        return jsonify({"error": "Вкажіть параметр ?group=НазваГрупи"}), 400

    html = fetch_ukd({"n": "700", "grp": group})
    if not html:
        return jsonify({"error": "Не вдалося підключитись до сервера УКД"}), 502

    lessons = parse_schedule_html(html)

    return jsonify({
        "group":   group,
        "lessons": lessons,
        "count":   len(lessons),
    })


@app.route("/api/health")
def health():
    """Перевірка що проксі живий."""
    ukd_ok = fetch_ukd({"n": "700"}) is not None
    return jsonify({
        "proxy": "ok",
        "ukd_server": "ok" if ukd_ok else "unreachable",
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=False)
