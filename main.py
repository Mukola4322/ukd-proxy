"""
UKD Schedule Proxy Server v4
Групи на сайті УКД завантажуються через JavaScript після вибору факультету.
Тому перебираємо всі відомі факультети (1001-1010) і збираємо групи через POST.
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

HEADERS = {
    "Content-Type": "application/x-www-form-urlencoded",
    "User-Agent": "Mozilla/5.0",
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


def fetch_get(params: dict) -> str | None:
    try:
        resp = requests.get(UKD_BASE, params=params, headers=HEADERS, timeout=15)
        return decode_html(resp)
    except Exception as e:
        print(f"GET error: {e}")
        return None


def fetch_post(data: dict) -> str | None:
    try:
        resp = requests.post(UKD_BASE, data=data, headers=HEADERS, timeout=15)
        return decode_html(resp)
    except Exception as e:
        print(f"POST error: {e}")
        return None


def strip_tags(html: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", html, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    for ent, ch in [("&nbsp;"," "),("&amp;","&"),("&lt;","<"),("&gt;",">")]:
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


def get_groups_for_faculty(faculty_id: str) -> list[dict]:
    """Робить POST щоб отримати групи конкретного факультету."""
    html = fetch_post({
        "n":       "700",
        "faculty": faculty_id,
        "setVedP": "1",
    })
    if not html:
        return []
    # Шукаємо групи в select name="group"
    # Спершу знаходимо блок після faculty select
    matches = re.findall(
        r'<option\s+value=["\']?(\d+)["\']?[^>]*>\s*([^<]+?)\s*</option>',
        html, re.IGNORECASE
    )
    groups = []
    for value, text in matches:
        text = text.strip()
        # Групи мають формат типу: КІПЗс-24-3, ІТ-21, МЕ-11-1
        if re.search(r'[А-ЯҐЄІЇа-яґєії]{1,8}-\d{2}', text):
            groups.append({"id": value, "name": text, "faculty_id": faculty_id})
    return groups


def get_all_groups() -> list[dict]:
    """Збирає групи з усіх факультетів."""
    # Спочатку отримуємо список факультетів з головної сторінки
    main_html = fetch_get({"n": "700"})
    if not main_html:
        return []

    faculty_ids = re.findall(
        r'<option\s+value=["\']?(\d{4})["\']?',
        main_html, re.IGNORECASE
    )
    faculty_ids = list(set(faculty_ids))
    print(f"Found faculty IDs: {faculty_ids}")

    all_groups = []
    for fid in faculty_ids:
        groups = get_groups_for_faculty(fid)
        print(f"Faculty {fid}: {len(groups)} groups")
        all_groups.extend(groups)

    return all_groups


def find_group_id(group_name: str) -> tuple[str | None, str | None]:
    """Повертає (group_id, faculty_id) для назви групи."""
    all_groups = get_all_groups()
    name_lower = group_name.strip().lower()

    # Точний збіг
    for g in all_groups:
        if g["name"].strip().lower() == name_lower:
            return g["id"], g["faculty_id"]

    # Частковий збіг
    for g in all_groups:
        if name_lower in g["name"].strip().lower():
            return g["id"], g["faculty_id"]

    return None, None


def fetch_schedule_by_id(group_id: str, faculty_id: str) -> str | None:
    """Завантажує розклад по числовому ID групи."""
    # Спробуємо різні варіанти параметрів
    html = fetch_post({
        "n":       "700",
        "faculty": faculty_id,
        "group":   group_id,
        "setVedP": "1",
    })
    if html and "<tr" in html:
        return html

    # Альтернатива — GET з grp=ID
    html = fetch_get({"n": "700", "grp": group_id})
    return html


def parse_cell(cell_html: str, pair_num: int, day_idx: int) -> dict | None:
    clean = strip_tags(cell_html)
    if not clean or not clean.replace(" ", "").replace("\n", ""):
        return None
    lines = [l.strip() for l in re.split(r"\n+", clean) if l.strip()]
    if not lines:
        return None
    subject     = lines[0]
    teacher     = lines[1] if len(lines) > 1 else ""
    room        = lines[2] if len(lines) > 2 else ""
    lesson_type = detect_type(clean)
    if not room:
        m = re.search(r"([А-ЯҐЄІЇа-яґєії]-?\d+|\d{3,4})", clean)
        if m:
            room = m.group(1)
    times = LESSON_TIMES.get(pair_num, {"start": "??:??", "end": "??:??"})
    return {
        "pairNumber": pair_num, "dayOfWeek": day_idx, "dayName": DAYS[day_idx],
        "subject": subject, "teacher": teacher, "room": room,
        "type": lesson_type, "timeStart": times["start"], "timeEnd": times["end"],
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
    return jsonify({"service": "UKD Schedule Proxy", "version": "4.0"})


@app.route("/api/groups")
def get_groups_route():
    groups = get_all_groups()
    if not groups:
        return jsonify({"error": "Не вдалося підключитись до сервера УКД"}), 502
    names = sorted(set(g["name"] for g in groups))
    return jsonify({"groups": names, "count": len(names)})


@app.route("/api/schedule")
def get_schedule():
    group = request.args.get("group", "").strip()
    if not group:
        return jsonify({"error": "Вкажіть ?group=НазваГрупи"}), 400

    group_id, faculty_id = find_group_id(group)
    if not group_id:
        return jsonify({
            "error": f"Групу '{group}' не знайдено. Перевір /api/groups",
            "group": group, "lessons": [], "count": 0
        }), 404

    html = fetch_schedule_by_id(group_id, faculty_id)
    if not html:
        return jsonify({"error": "Не вдалося завантажити розклад"}), 502

    lessons = parse_schedule_html(html)
    return jsonify({"group": group, "group_id": group_id, "lessons": lessons, "count": len(lessons)})


@app.route("/api/health")
def health():
    html = fetch_get({"n": "700"})
    return jsonify({"proxy": "ok", "ukd_server": "ok" if html else "unreachable"})


@app.route("/api/debug")
def debug():
    group = request.args.get("group", "").strip()

    # Крок 1: головна сторінка
    main_html = fetch_get({"n": "700"})
    if not main_html:
        return jsonify({"error": "УКД недоступний"}), 502

    faculty_ids = list(set(re.findall(r'<option\s+value=["\']?(\d{4})["\']?', main_html)))
    result = {
        "step1_faculty_ids": faculty_ids,
        "main_html_length": len(main_html),
    }

    # Крок 2: групи першого факультету
    if faculty_ids:
        fid = faculty_ids[0]
        faculty_html = fetch_post({"n": "700", "faculty": fid, "setVedP": "1"})
        if faculty_html:
            matches = re.findall(
                r'<option\s+value=["\']?(\d+)["\']?[^>]*>\s*([^<]+?)\s*</option>',
                faculty_html, re.IGNORECASE
            )
            result["step2_faculty_sample"] = fid
            result["step2_options_found"] = [
                {"id": v, "text": t.strip()} for v, t in matches[:30]
            ]
            result["step2_html_preview"] = faculty_html[:1500]

    # Крок 3: якщо вказана група
    if group:
        group_id, faculty_id = find_group_id(group)
        result["step3_group"] = group
        result["step3_group_id"] = group_id
        result["step3_faculty_id"] = faculty_id

        if group_id:
            sched_html = fetch_schedule_by_id(group_id, faculty_id)
            if sched_html:
                rows = re.findall(r"<tr[^>]*>(.*?)</tr>", sched_html, re.DOTALL | re.IGNORECASE)
                parsed = []
                for i, row in enumerate(rows[:15]):
                    cells = re.findall(r"<td[^>]*>(.*?)</td>", row, re.DOTALL | re.IGNORECASE)
                    parsed.append({
                        "row": i,
                        "cells": [strip_tags(c)[:80] for c in cells[:8]]
                    })
                result["step3_schedule_rows"] = parsed
                result["step3_schedule_html"] = sched_html[:2000]

    return jsonify(result)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=False)
