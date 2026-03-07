"""
UKD Schedule Proxy v9 — перевірка доступності + всі варіанти запиту
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
    "четвер": 3, "п'ятниця": 4, "п`ятниця": 4, "субота": 5,
}
DAYS_UA = ["Понеділок", "Вівторок", "Середа", "Четвер", "П'ятниця", "Субота"]


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


def try_all_methods(group: str, d_from: str, d_to: str) -> dict:
    """Пробує всі варіанти запиту і повертає результати кожного."""
    results = {}
    form_data = {
        "faculty": "0", "teacher": "", "course": "0",
        "group": group, "sdate": d_from, "edate": d_to,
    }

    headers_browser = {
        "Content-Type": "application/x-www-form-urlencoded",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120",
        "Referer": UKD_BASE + "?n=700",
        "Origin": "http://195.162.83.28",
        "Accept": "text/html,application/xhtml+xml,*/*",
        "Accept-Language": "uk-UA,uk;q=0.9",
    }

    # Варіант 1: POST на ?n=700
    try:
        s = requests.Session()
        s.get(UKD_BASE + "?n=700", headers=headers_browser, timeout=10)
        r = s.post(UKD_BASE + "?n=700", data=form_data,
                   headers=headers_browser, timeout=15)
        html = decode_resp(r)
        results["v1_post_n700_url"] = {
            "status": r.status_code,
            "has_rozklad": "Розклад групи" in html,
            "html_len": len(html),
            "preview": html[html.find("Розклад"):html.find("Розклад")+500] if "Розклад" in html else html[3000:3500],
        }
    except Exception as e:
        results["v1_post_n700_url"] = {"error": str(e)}

    # Варіант 2: POST з n=700 в тілі
    try:
        data2 = {**form_data, "n": "700"}
        r = requests.post(UKD_BASE, data=data2, headers=headers_browser, timeout=15)
        html = decode_resp(r)
        results["v2_post_n_in_body"] = {
            "status": r.status_code,
            "has_rozklad": "Розклад групи" in html,
            "html_len": len(html),
        }
    except Exception as e:
        results["v2_post_n_in_body"] = {"error": str(e)}

    # Варіант 3: GET з усіма параметрами
    try:
        params = {**form_data, "n": "700"}
        r = requests.get(UKD_BASE, params=params, headers=headers_browser, timeout=15)
        html = decode_resp(r)
        results["v3_get_all_params"] = {
            "status": r.status_code,
            "has_rozklad": "Розклад групи" in html,
            "html_len": len(html),
            "url_used": r.url,
        }
    except Exception as e:
        results["v3_get_all_params"] = {"error": str(e)}

    # Варіант 4: GET тільки group і дати
    try:
        r = requests.get(UKD_BASE, params={
            "n": "700", "group": group, "sdate": d_from, "edate": d_to
        }, headers=headers_browser, timeout=15)
        html = decode_resp(r)
        results["v4_get_minimal"] = {
            "status": r.status_code,
            "has_rozklad": "Розклад групи" in html,
            "html_len": len(html),
        }
    except Exception as e:
        results["v4_get_minimal"] = {"error": str(e)}

    # Варіант 5: POST без faculty/course
    try:
        r = requests.post(UKD_BASE + "?n=700", data={
            "teacher": "", "group": group, "sdate": d_from, "edate": d_to,
        }, headers=headers_browser, timeout=15)
        html = decode_resp(r)
        results["v5_post_no_faculty"] = {
            "status": r.status_code,
            "has_rozklad": "Розклад групи" in html,
            "html_len": len(html),
        }
    except Exception as e:
        results["v5_post_no_faculty"] = {"error": str(e)}

    return results


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
        day_raw = m.group(2).lower().strip().replace("`","'")
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
            content = strip_tags(" ".join(cells[2:] if len(cells)>2 else cells[1:])).strip()
            if not content:
                continue
            lines = [l.strip() for l in re.split(r'\n+', content) if l.strip()]
            subject = lines[0] if lines else content
            teacher = lines[1] if len(lines) > 1 else ""
            room = ""
            rm = re.search(r'ауд\.?\s*(\S+)', content, re.IGNORECASE)
            if rm:
                room = "ауд." + rm.group(1)
                subject = re.sub(r'\s*ауд\.?\s*\S+','',subject).strip()
                teacher = re.sub(r'\s*ауд\.?\s*\S+','',teacher).strip()
            elif len(lines) > 2:
                room = lines[-1]
            t = LESSON_TIMES.get(pn, {"start":"??:??","end":"??:??"})
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
    return jsonify({"service": "UKD Proxy", "version": "9.0"})


@app.route("/api/schedule")
def get_schedule():
    group = request.args.get("group","").strip()
    if not group:
        return jsonify({"error": "Вкажіть ?group=КІПЗс-24-3"}), 400
    d_from, d_to = get_week_dates(int(request.args.get("week", 0)))

    # Пробуємо всі варіанти поки не знайдемо розклад
    form_data = {"faculty":"0","teacher":"","course":"0","group":group,"sdate":d_from,"edate":d_to}
    hdrs = {"Content-Type":"application/x-www-form-urlencoded",
            "User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "Referer": UKD_BASE+"?n=700", "Origin":"http://195.162.83.28"}

    html = None
    for attempt in [
        lambda: requests.post(UKD_BASE+"?n=700", data=form_data, headers=hdrs, timeout=15),
        lambda: requests.post(UKD_BASE, data={**form_data,"n":"700"}, headers=hdrs, timeout=15),
        lambda: requests.get(UKD_BASE, params={**form_data,"n":"700"}, headers=hdrs, timeout=15),
    ]:
        try:
            r = attempt()
            h = decode_resp(r)
            if "Розклад групи" in h:
                html = h
                break
        except Exception:
            pass

    if not html:
        return jsonify({"error": f"Не вдалося отримати розклад для '{group}'",
                        "lessons": [], "count": 0}), 502

    lessons = parse_schedule_html(html)
    return jsonify({"group": group, "week": {"from":d_from,"to":d_to},
                    "lessons": lessons, "count": len(lessons)})


@app.route("/api/health")
def health():
    try:
        r = requests.get(UKD_BASE+"?n=700", timeout=10)
        ok = r.status_code == 200
    except Exception:
        ok = False
    return jsonify({"proxy":"ok","ukd_server":"ok" if ok else "unreachable"})


@app.route("/api/debug")
def debug():
    group = request.args.get("group","КІПЗс-24-3").strip()
    d_from, d_to = get_week_dates(0)
    results = try_all_methods(group, d_from, d_to)
    return jsonify({
        "group": group, "week": {"from": d_from, "to": d_to},
        "attempts": results,
        "summary": {k: v.get("has_rozklad", False) for k,v in results.items() if "error" not in v}
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=False)
