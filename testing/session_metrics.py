"""Aggregate anonymous metrics from Raven session logs (read-only, offline).

Usage:
    python testing/session_metrics.py [LOG_OR_DIR ...] [--md | --json] [--since YYYY-MM-DD]

Only machine-readable event fields are read (turn.route, [TURN], stt.groq.complete,
confirmation_*, voice_input_discarded, stt.language_guard). Transcript text, dialogue
lines and request ids are never copied into the report.
Default output is an aligned text overview for the terminal; --md gives Markdown tables.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

DEFAULT_DIR = Path("logs/sessions")
FILE_DATE = re.compile(r"session-(\d{4})(\d{2})(\d{2})T")
KV = re.compile(r"(\w+)=([^\s|]+)")
TURN_FIELDS = ("endpoint", "recognition_ready", "llm_first_text", "first_phrase", "tts_submit", "total")


def percentile(values, q):
    """Nearest-rank percentile; None for an empty sample."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(q * len(ordered)))
    return ordered[min(rank, len(ordered)) - 1]


def route_group(fields):
    """Coarse class of a turn: action/search, clarification or plain conversation."""
    if fields.get("natural_action") == "True":
        return "action"
    if "clarif" in (fields.get("type") or ""):
        return "clarify"
    return "chat"


def kv(line):
    return dict(KV.findall(line))


def parse_turn_line(line):
    out = {}
    for name in TURN_FIELDS:
        m = re.search(rf"\b{name}=(\d+)ms", line)
        if m:
            out[name] = int(m.group(1))
    return out


class Metrics:
    def __init__(self):
        self.sessions = 0
        self.routes = Counter()          # (source, scored, eligible, type, route)
        self.groups = Counter()          # (source, group)
        self.voice_turns = 0
        self.text_turns = 0
        self.voice_unscored = 0
        self.voice_eligible = 0
        self.voice_natural_action = 0
        self.discarded = Counter()
        self.language_reject = 0
        self.groq = Counter()
        self.groq_ms = []
        self.groq_errors = Counter()
        self.confirm_capture = Counter()
        self.confirm_voice = Counter()
        self.confirm_button = Counter()
        self.confirm_rejected = Counter()
        self.turn = defaultdict(list)
        self.turn_n = 0

    def add_line(self, line):
        for marker, handler in self._handlers:
            if marker in line:
                handler(self, line)
                return

    def _route(self, line):
        f = kv(line.split("turn.route ", 1)[1])
        src = f.get("source", "?")
        self.routes[(src, f.get("scored"), f.get("eligible"), f.get("type"), f.get("route"))] += 1
        self.groups[(src, route_group(f))] += 1
        if src != "voice":
            self.text_turns += 1
            return
        self.voice_turns += 1
        self.voice_unscored += f.get("scored") == "False"
        self.voice_eligible += f.get("eligible") == "True"
        self.voice_natural_action += f.get("natural_action") == "True"

    def _discarded(self, line):
        self.discarded[kv(line).get("reason", "?")] += 1

    def _language_guard(self, line):
        self.language_reject += 1

    def _groq(self, line):
        f = kv(line)
        ok = f.get("success") == "True"
        self.groq["ok" if ok else "failed"] += 1
        if not ok:
            self.groq_errors[f.get("error_class", "?")] += 1
        ms = re.search(r"api_roundtrip_ms=([\d.]+)", line)
        if ms:
            self.groq_ms.append(float(ms.group(1)))

    def _confirm_capture(self, line):
        self.confirm_capture[kv(line).get("outcome", "?")] += 1

    def _confirm_voice(self, line):
        self.confirm_voice[kv(line).get("semantic", "?")] += 1

    def _confirm_button(self, line):
        self.confirm_button[kv(line).get("outcome", "?")] += 1

    def _confirm_rejected(self, line):
        self.confirm_rejected[line.split("confirmation_reply_rejected=", 1)[1].split()[0]] += 1

    def _turn_line(self, line):
        self.turn_n += 1
        for k, v in parse_turn_line(line).items():
            self.turn[k].append(v)

    _handlers = (
        ("turn.route ", _route),
        ("voice_input_discarded", _discarded),
        ("stt.language_guard decision=reject", _language_guard),
        ("stt.groq.complete", _groq),
        ("confirmation_capture ", _confirm_capture),
        ("confirmation_voice ", _confirm_voice),
        ("confirmation_button ", _confirm_button),
        ("confirmation_reply_rejected=", _confirm_rejected),
        ("[TURN] ", _turn_line),
    )

    def add_file(self, path):
        self.sessions += 1
        with open(path, encoding="utf-8", errors="replace") as handle:
            for line in handle:
                self.add_line(line)

    def summary(self):
        def stat(values):
            def r(x):
                return None if x is None else round(x)

            return {"n": len(values), "p50": r(percentile(values, 0.5)),
                    "p95": r(percentile(values, 0.95)), "max": r(max(values) if values else None)}

        vt = self.voice_turns
        return {
            "sessions": self.sessions,
            "voice_turns": vt,
            "text_turns": self.text_turns,
            "voice_unscored": self.voice_unscored,
            "voice_unscored_share": round(self.voice_unscored / vt, 3) if vt else None,
            "voice_action_eligible": self.voice_eligible,
            "voice_natural_action": self.voice_natural_action,
            "route_groups": {f"{src}/{grp}": n for (src, grp), n in sorted(self.groups.items())},
            "routes": {" / ".join(map(str, k)): v for k, v in sorted(self.routes.items(), key=lambda i: -i[1])},
            "discarded_reasons": dict(self.discarded),
            "language_guard_rejects": self.language_reject,
            "groq_requests": dict(self.groq),
            "groq_errors": dict(self.groq_errors),
            "groq_roundtrip_ms": stat(self.groq_ms),
            "confirmation_capture": dict(self.confirm_capture),
            "confirmation_voice_semantic": dict(self.confirm_voice),
            "confirmation_button": dict(self.confirm_button),
            "confirmation_rejected": dict(self.confirm_rejected),
            "turn_ms": {k: stat(self.turn[k]) for k in TURN_FIELDS},
            "turn_records": self.turn_n,
        }


GROUP_LABELS = {"chat": "розмова", "clarify": "уточнення", "action": "дія / пошук"}
DISCARD_LABELS = {
    "empty_transcript": "порожній результат (тиша)",
    "interrupted": "перервано (стоп / нове введення)",
    "recognition_unreliable": "розпізнавання ненадійне",
}
STAGE_LABELS = (
    ("endpoint", "очікування кінця фрази"),
    ("recognition_ready", "до готового тексту"),
    ("llm_first_text", "перший текст LLM"),
    ("first_phrase", "до першої фрази"),
    ("tts_submit", "до подання на озвучення"),
    ("total", "хід загалом"),
)


def pct(part, whole):
    return f"{100 * part / whole:.0f}%" if whole else "—"


def sec(ms):
    return "—" if ms is None else f"{ms / 1000:.2f}"


def bar(part, whole, width=20):
    filled = round(width * part / whole) if whole else 0
    return "█" * filled + "░" * (width - filled)


def table(headers, rows, markdown):
    """Render rows as a Markdown table or as aligned plain text."""
    rows = [[str(c) for c in r] for r in rows]
    if markdown:
        out = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
        return out + ["| " + " | ".join(r) + " |" for r in rows]
    widths = [max(len(h), *(len(r[i]) for r in rows)) if rows else len(h) for i, h in enumerate(headers)]

    def line(cells):
        return "  ".join(c.ljust(w) if i == 0 else c.rjust(w) for i, (c, w) in enumerate(zip(cells, widths)))

    return ["  " + line(headers), "  " + "  ".join("─" * w for w in widths)] + ["  " + line(r) for r in rows]


def counter_rows(mapping, total=None, labels=None):
    total = total if total is not None else sum(mapping.values())
    ordered = sorted(mapping.items(), key=lambda i: -i[1])
    return [[(labels or {}).get(k, k), v, pct(v, total)] for k, v in ordered]


def render(summary, per_session=None, markdown=False):
    s = summary
    vt, tt = s["voice_turns"], s["text_turns"]
    h = (lambda t: f"\n## {t}\n") if markdown else (lambda t: f"\n{t.upper()}\n{'═' * len(t)}")
    out = ["# Зведення метрик журналів сесій" if markdown else "ЗВЕДЕННЯ МЕТРИК ЖУРНАЛІВ СЕСІЙ", ""]
    out.append(f"Сесій: {s['sessions']} · ходів `[TURN]`: {s['turn_records']} · голосових: {vt} · текстових: {tt}")
    out.append("Лише числові події, без тексту реплік. Перцентилі — за найближчим рангом; n завжди вказано.")

    out.append(h("Огляд"))
    groq = s["groq_requests"]
    groq_total = sum(groq.values())
    voice_actions = s["route_groups"].get("voice/action", 0)
    unscored = f"{s['voice_unscored']} із {vt} ({pct(s['voice_unscored'], vt)})"
    key = [
        ["Голосові ходи без confidence (шлях Groq)", unscored],
        ["Голосові ходи з правом на дію за довірою до STT", f"{s['voice_action_eligible']} із {vt}"],
        ["Голосові ходи з наміром дії / пошуку", f"{voice_actions} із {vt} ({pct(voice_actions, vt)})"],
        ["Запити Groq STT успішні", f"{groq.get('ok', 0)} із {groq_total} ({pct(groq.get('ok', 0), groq_total)})"],
        ["Відкинуті голосові входи", sum(s["discarded_reasons"].values())],
        ["Відхилені відповіді підтвердження", sum(s["confirmation_rejected"].values())],
        ["Відхилення мовного фільтра (language_guard)", s["language_guard_rejects"]],
    ]
    out += table(["Показник", "Значення"], key, markdown)

    if per_session:
        out.append(h("Сесії"))
        out += table(["Сесія (дата / час UTC)", "голос", "текст", "без conf.", "Groq ок/збій", "p50 першої фрази, с"],
                     per_session, markdown)

    out.append(h("Маршрути ходів"))
    groups = Counter()
    for k, n in s["route_groups"].items():
        src, grp = k.split("/")
        groups[(src, grp)] = n
    rows = [[f"{'голос' if src == 'voice' else 'текст'} · {GROUP_LABELS.get(grp, grp)}", n,
             pct(n, vt if src == "voice" else tt), bar(n, vt if src == "voice" else tt)]
            for (src, grp), n in sorted(groups.items(), key=lambda i: (i[0][0] != "voice", -i[1]))]
    out += table(["Джерело · клас", "ходів", "частка", ""], rows, markdown)
    out.append("")
    out.append("Типи маршрутів докладно (source / type / route; усі зі scored=False/eligible=False, якщо не зазначено):")
    detail = []
    for key_str, n in s["routes"].items():
        src, scored, eligible, typ, route = key_str.split(" / ")
        flags = "" if (scored, eligible) == ("False", "False") else f" [scored={scored}, eligible={eligible}]"
        detail.append([f"{src} · {typ} → {route}{flags}", n])
    out += table(["Маршрут", "ходів"], detail, markdown)

    out.append(h("Втрачені та відкинуті входи"))
    out += table(["Причина", "разів", "частка"],
                 counter_rows(s["discarded_reasons"], labels=DISCARD_LABELS), markdown)

    out.append(h("Підтвердження дій"))
    rows = []
    for title, mapping in (("захоплення відповіді", s["confirmation_capture"]),
                           ("голосова відповідь (семантика)", s["confirmation_voice_semantic"]),
                           ("кнопка в UI", s["confirmation_button"]),
                           ("відхилено як застаріле / некорельоване", s["confirmation_rejected"])):
        rows += [[title, k, v] for k, v in sorted(mapping.items(), key=lambda i: -i[1])] or []
        if not mapping:
            rows.append([title, "—", 0])
    out += table(["Етап", "результат", "разів"], rows, markdown)

    if s["groq_errors"]:
        out.append(h("Помилки Groq за класом"))
        out += table(["Клас", "разів", "частка"], counter_rows(s["groq_errors"]), markdown)

    out.append(h("Затримки, секунди"))
    g = s["groq_roundtrip_ms"]
    rows = [["запит Groq STT (roundtrip)", g["n"], sec(g["p50"]), sec(g["p95"]), sec(g["max"])]]
    for name, label in STAGE_LABELS:
        v = s["turn_ms"][name]
        rows.append([label, v["n"], sec(v["p50"]), sec(v["p95"]), sec(v["max"])])
    out += table(["Етап", "n", "p50", "p95", "max"], rows, markdown)
    out.append("")
    out.append("Етапи `[TURN]` відраховуються від кінця очікування тиші (endpoint). «Хід загалом» завершується на "
               "завершенні обробки, а не на кінці озвучення; «до подання на озвучення» не дорівнює першому звуку.")
    return "\n".join(out) + "\n"


def session_rows(per_file):
    rows = []
    for name, m in per_file:
        found = FILE_DATE.search(name)
        stamp = re.search(r"T(\d{2})(\d{2})(\d{2})", name)
        label = f"{'-'.join(found.groups())} {':'.join(stamp.groups())}" if found and stamp else name
        sm = m.summary()
        groq = sm["groq_requests"]
        rows.append([label, sm["voice_turns"], sm["text_turns"], sm["voice_unscored"],
                     f"{groq.get('ok', 0)}/{groq.get('failed', 0)}", sec(sm["turn_ms"]["first_phrase"]["p50"])])
    return rows


def collect(paths, since):
    files = []
    for p in paths or [DEFAULT_DIR]:
        p = Path(p)
        files += sorted(p.glob("session-*.log")) if p.is_dir() else [p]
    if since:
        def keep(f):
            m = FILE_DATE.search(f.name)
            return not m or "-".join(m.groups()) >= since
        files = [f for f in files if keep(f)]
    return files


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("paths", nargs="*")
    parser.add_argument("--json", action="store_true", help="машинозчитуваний вивід")
    parser.add_argument("--md", action="store_true", help="таблиці Markdown замість вирівняного тексту")
    parser.add_argument("--since", help="YYYY-MM-DD, за датою в назві файла")
    args = parser.parse_args(argv)
    total, per_file = Metrics(), []
    for f in collect(args.paths, args.since):
        one = Metrics()
        one.add_file(f)
        total.add_file(f)
        per_file.append((f.name, one))
    summary = total.summary()
    if args.json:
        sys.stdout.write(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    else:
        sys.stdout.write(render(summary, session_rows(per_file), markdown=args.md))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
