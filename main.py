"""
UKD Schedule Proxy Server v5
Форма УКД: факультет → курс → група (3 кроки)
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


def post_form(data: dict) -> str | None:
    try:
        resp = requests.post(UKD_BASE, data=data, headers=HEADERS, timeout=15)
        return decode_html(resp)
    except Exception as e:
        print(f"POST error: {e}")
        return None


def get_page(params: dict) -> str | None:
    try:
        resp = requests.get(UKD_BASE, params=params, headers=HEADERS, timeout=15)
        return decode_html(resp)
    except Exception as e:
        print(f"GET error: {e}")
        return None


def strip_tags(html: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", html, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    for ent, ch in [("&nbsp;"," "),("&amp;","&"),("&lt;","<"),("&gt;",">")]:
        text = text.replace(ent, ch)
    text = re.sub(r"&#\d+;", "", text)
    text = re.sub(r"\s{2,}", " ", text)
    return text.strip()


def parse_options(html: str, select_name: str) -> list[dict]:
    """Витягує options з конкретного select по name."""
    # Знаходимо потрібний select
    sel_pattern = rf'<select[^>]+name=["\']?{select_name}["\']?[^>]*>(.*?)</select>'
    sel_match = re.search(sel_pattern, html, re.DOTALL | re.IGNORECASE)
    if not sel_match:
        return []
    sel_html = sel_match.group(1)
    matches = re.findall(
        r'<option\s+value=["\']?([^"\'>\s]*)["\']?[^>]*>\s*([^<]*?)\s*</option>',
        sel_html, re.IGNORECASE
    )
    return [{"id": v, "name": t.strip()} for v, t in matches if v and v != "0"]


def get_all_groups() -> list[dict]:
    """Перебирає факультети і курси щоб зібрати всі групи."""
    main_html = get_page({"n": "700"})
    if not main_html:
        return []

    faculties = parse_options(main_html, "faculty")
    print(f"Faculties: {[f['id'] for f in faculties]}")

    all_groups = []

    for faculty in faculties:
        fid = faculty["id"]
        # Крок 2: вибираємо факультет → отримуємо курси
        html2 = post_form({"n": "700", "faculty": fid, "setVedP": "1"})
        if not html2:
            continue

        courses = parse_options(html2, "course")
        if not courses:
            # Спробуємо без курсу — може вже є групи
            groups = parse_options(html2, "group")
            for g in groups:
                if re.search(r'[А-ЯҐЄІЇа-яґєії]{1,8}-\d{2}', g["name"]):
                    all_groups.append({
                        "id": g["id"], "name": g["name"],
                        "faculty_id": fid, "course": "0"
                    })
            continue

        for course in courses:
            cid = course["id"]
            # Крок 3: вибираємо курс → отримуємо групи
            html3 = post_form({
                "n": "700",
                "faculty": fid,
                "course": cid,
                "setVedP": "1",
            })
            if not html3:
                continue

            groups = parse_options(html3, "group")
            for g in groups:
                if re.search(r'[А-ЯҐЄІЇа-яґєії]{1,8}-\d{2}', g["name"]):
                    all_groups.append({
                        "id": g["id"], "name": g["name"],
                        "faculty_id": fid, "course": cid
                    })

    print(f"Total groups found: {len(all_groups)}")
    return all_groups


def find_group(group_name: str) -> dict | None:
    all_groups = get_all_groups()
    name_lower = group_name.strip().lower()
    for g in all_groups:
        if g["name"].strip().lower() == name_lower:
            return g
    for g in all_groups:
        if name_lower in g["name"].strip().lower():
            return g
    return None


def fetch_schedule_html(group: dict) -> str | None:
    """Завантажує HTML розкладу для знайденої групи."""
    # POST з усіма параметрами форми
    html = post_form({
        "n":       "700",
        "faculty": group["faculty_id"],
        "course":  group["course"],
        "group":   group["id"],
        "setVedP": "1",
    })
    if html and re.search(r'<tr[^>]*>.*?<td', html, re.DOTALL):
        return html
    # Fallback: GET
    return get_page({"n": "700", "grp": group["id"]})


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
    return jsonify({"service": "UKD Schedule Proxy", "version": "5.0"})


@app.route("/api/groups")
def get_groups_route():
    groups = get_all_groups()
    if not groups:
        return jsonify({"error": "Не вдалося підключитись до УКД"}), 502
    names = sorted(set(g["name"] for g in groups))
    return jsonify({"groups": names, "count": len(names)})


@app.route("/api/schedule")
def get_schedule():
    group_name = request.args.get("group", "").strip()
    if not group_name:
        return jsonify({"error": "Вкажіть ?group=НазваГрупи"}), 400

    group = find_group(group_name)
    if not group:
        return jsonify({
            "error": f"Групу '{group_name}' не знайдено",
            "group": group_name, "lessons": [], "count": 0
        }), 404

    html = fetch_schedule_html(group)
    if not html:
        return jsonify({"error": "Не вдалося завантажити розклад"}), 502

    lessons = parse_schedule_html(html)
    return jsonify({
        "group": group_name, "group_id": group["id"],
        "lessons": lessons, "count": len(lessons)
    })


@app.route("/api/health")
def health():
    html = get_page({"n": "700"})
    return jsonify({"proxy": "ok", "ukd_server": "ok" if html else "unreachable"})


@app.route("/api/debug")
def debug():
    group_name = request.args.get("group", "").strip()

    main_html = get_page({"n": "700"})
    if not main_html:
        return jsonify({"error": "УКД недоступний"}), 502

    faculties = parse_options(main_html, "faculty")
    result = {"step1_faculties": faculties}

    if faculties:
        fid = faculties[0]["id"]
        html2 = post_form({"n": "700", "faculty": fid, "setVedP": "1"})
        if html2:
            courses  = parse_options(html2, "course")
            result["step2_faculty_id"] = fid
            result["step2_courses"] = courses

            if courses:
                cid = courses[0]["id"]
                html3 = post_form({"n":"700","faculty":fid,"course":cid,"setVedP":"1"})
                if html3:
                    groups = parse_options(html3, "group")
                    result["step3_course_id"] = cid
                    result["step3_groups_sample"] = groups[:20]

    if group_name:
        found = find_group(group_name)
        result["search_group"] = group_name
        result["found"] = found
        if found:
            sched_html = fetch_schedule_html(found)
            if sched_html:
                rows = re.findall(r"<tr[^>]*>(.*?)</tr>", sched_html, re.DOTALL|re.IGNORECASE)
                parsed = []
                for i, row in enumerate(rows[:15]):
                    cells = re.findall(r"<td[^>]*>(.*?)</td>", row, re.DOTALL|re.IGNORECASE)
                    parsed.append({"row": i, "cells": [strip_tags(c)[:80] for c in cells[:8]]})
                result["schedule_rows"] = parsed

    return jsonify(result)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=False)
