"""
UKD Schedule Proxy Server v3
Сервер УКД використовує числові ID для груп — спочатку знаходимо ID по назві групи.
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


def fetch_html(params: dict, data: dict = None) -> str | None:
    """GET або POST запит до УКД з автовизначенням кодування."""
    try:
        if data:
            resp = requests.post(UKD_BASE, params=params, data=data, timeout=15)
        else:
            resp = requests.get(UKD_BASE, params=params, timeout=15)

        for enc in ("windows-1251", "utf-8", "koi8-u"):
            resp.encoding = enc
            text = resp.text
            if any(c in text for c in "АБВГДЕЄЖЗІЇаб"):
                return text

        resp.encoding = "windows-1251"
        return resp.text
    except requests.RequestException as e:
        print(f"Fetch error: {e}")
        return None


def parse_groups_from_html(html: str) -> list[dict]:
    """
    Витягує всі групи з усіх факультетів.
    HTML містить: <option value="ЧИСЛО">Назва групи</option>
    """
    # Знаходимо секцію з групами (select name="group" або схоже)
    # Шукаємо всі option з числовим value та текстом що схожий на групу
    groups = []
    
    # Шукаємо всі <option value="число">Текст</option>
    matches = re.findall(
        r'<option\s+value=["\']?(\d+)["\']?[^>]*>\s*([^<]+?)\s*</option>',
        html, re.IGNORECASE
    )
    
    for value, text in matches:
        text = text.strip()
        # Фільтруємо — групи мають типовий формат: букви-цифри-цифри
        # Наприклад: КІПЗс-24-3, ІТ-21, МЕ-11
        if re.search(r'[А-ЯҐЄІЇа-яґєії]{1,6}[а-яА-Яє-ї]?-\d{2}', text):
            groups.append({"id": value, "name": text})
    
    return groups


def find_group_id(group_name: str) -> str | None:
    """Знаходить числовий ID групи по її назві."""
    # Завантажуємо головну форму
    html = fetch_html({"n": "700"})
    if not html:
        return None
    
    groups = parse_groups_from_html(html)
    
    # Точний збіг
    for g in groups:
        if g["name"].strip().lower() == group_name.strip().lower():
            return g["id"]
    
    # Частковий збіг
    for g in groups:
        if group_name.strip().lower() in g["name"].strip().lower():
            return g["id"]
    
    return None


def get_all_groups_with_ids() -> list[dict]:
    """Повертає всі групи з їх ID."""
    html = fetch_html({"n": "700"})
    if not html:
        return []
    return parse_groups_from_html(html)


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


def parse_cell(cell_html: str, pair_num: int, day_idx: int) -> dict | None:
    clean = strip_tags(cell_html)
    if not clean or not clean.replace(" ", "").replace("\n", ""):
        return None
    lines = [l.strip() for l in re.split(r"\n+", clean) if l.strip()]
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


def parse_schedule_html(html: str) -> list[dict]:
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


# ── Routes ─────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return jsonify({"service": "UKD Schedule Proxy", "version": "3.0"})


@app.route("/api/groups")
def get_groups():
    groups = get_all_groups_with_ids()
    if not groups:
        return jsonify({"error": "Не вдалося підключитись до сервера УКД"}), 502
    # Повертаємо тільки назви для сумісності з iOS-додатком
    names = [g["name"] for g in groups]
    return jsonify({"groups": names, "count": len(names)})


@app.route("/api/schedule")
def get_schedule():
    group = request.args.get("group", "").strip()
    if not group:
        return jsonify({"error": "Вкажіть ?group=НазваГрупи"}), 400

    # Крок 1: знаходимо числовий ID групи
    group_id = find_group_id(group)
    if not group_id:
        return jsonify({
            "error": f"Групу '{group}' не знайдено. Перевір назву через /api/groups",
            "group": group,
            "lessons": [],
            "count": 0
        }), 404

    # Крок 2: завантажуємо розклад по ID
    html = fetch_html({"n": "700", "grp": group_id})
    if not html:
        return jsonify({"error": "Не вдалося підключитись до сервера УКД"}), 502

    lessons = parse_schedule_html(html)
    return jsonify({
        "group":    group,
        "group_id": group_id,
        "lessons":  lessons,
        "count":    len(lessons),
    })


@app.route("/api/health")
def health():
    html = fetch_html({"n": "700"})
    ukd_ok = html is not None
    return jsonify({
        "proxy": "ok",
        "ukd_server": "ok" if ukd_ok else "unreachable"
    })


@app.route("/api/debug")
def debug():
    """Діагностика: показує знайдені групи та сирий HTML."""
    group = request.args.get("group", "").strip()

    # Спершу показуємо список груп
    main_html = fetch_html({"n": "700"})
    if not main_html:
        return jsonify({"error": "УКД недоступний"}), 502

    all_groups = parse_groups_from_html(main_html)

    result = {
        "all_groups_found": all_groups[:50],
        "groups_count": len(all_groups),
        "main_html_preview": main_html[:2000],
    }

    if group:
        group_id = find_group_id(group)
        result["searched_group"] = group
        result["found_group_id"] = group_id

        if group_id:
            schedule_html = fetch_html({"n": "700", "grp": group_id})
            if schedule_html:
                rows = re.findall(r"<tr[^>]*>(.*?)</tr>", schedule_html, re.DOTALL | re.IGNORECASE)
                parsed_rows = []
                for i, row in enumerate(rows[:20]):
                    cells = re.findall(r"<td[^>]*>(.*?)</td>", row, re.DOTALL | re.IGNORECASE)
                    parsed_rows.append({
                        "row_index": i,
                        "cells_count": len(cells),
                        "cells": [strip_tags(c)[:100] for c in cells[:8]]
                    })
                result["schedule_html_length"] = len(schedule_html)
                result["schedule_html_preview"] = schedule_html[:2000]
                result["schedule_rows"] = parsed_rows

    return jsonify(result)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=False)
