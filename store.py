"""Conversaciones, pausas y limite de mensajes (SQLite)."""
import os
import sqlite3
import time
from contextlib import contextmanager

DB = os.getenv("DB_PATH", "camara.db")
SCHEMA = """
CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY, phone TEXT, role TEXT, text TEXT, ts INTEGER);
CREATE INDEX IF NOT EXISTS ix_msg ON messages(phone, ts);
CREATE TABLE IF NOT EXISTS contacts(phone TEXT PRIMARY KEY, first_seen INTEGER,
    paused INTEGER DEFAULT 0, warned INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS processed_webhook_messages(message_id TEXT PRIMARY KEY, received_at INTEGER NOT NULL);
"""
FIELDS = {"paused", "warned"}


@contextmanager
def con():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA)
    try:
        with c:
            yield c
    finally:
        c.close()


def touch(phone: str) -> dict:
    """Registra el contacto si es nuevo. Devuelve sus datos y 'is_new'."""
    with con() as c:
        row = c.execute("SELECT * FROM contacts WHERE phone=?", (phone,)).fetchone()
        if row is None:
            c.execute("INSERT INTO contacts(phone, first_seen) VALUES(?,?)", (phone, int(time.time())))
            return {"phone": phone, "paused": 0, "warned": 0, "is_new": True}
        return {**dict(row), "is_new": False}


def set_flag(phone: str, field: str, value: int) -> None:
    assert field in FIELDS
    with con() as c:
        c.execute("INSERT OR IGNORE INTO contacts(phone, first_seen) VALUES(?,?)", (phone, int(time.time())))
        c.execute(f"UPDATE contacts SET {field}=? WHERE phone=?", (value, phone))


def log(phone: str, role: str, text: str) -> None:
    """role: customer | agent | human | handoff (derivacion a asesor) |
    notice (aviso automatico, no entra al historial de Claude)"""
    with con() as c:
        c.execute("INSERT INTO messages(phone, role, text, ts) VALUES(?,?,?,?)", (phone, role, text, int(time.time())))


def already_seen(message_id: str) -> bool:
    """Guarda el ID del webhook en SQLite; devuelve True si ya estaba registrado."""
    with con() as c:
        result = c.execute(
            "INSERT OR IGNORE INTO processed_webhook_messages(message_id, received_at) VALUES(?,?)",
            (message_id, int(time.time())),
        )
        return result.rowcount == 0


def count_recent(phone: str, seconds: int = 3600) -> int:
    with con() as c:
        return c.execute("SELECT COUNT(*) FROM messages WHERE phone=? AND role='customer' AND ts>=?",
                         (phone, int(time.time()) - seconds)).fetchone()[0]


def last_messages(phone: str, n: int = 10) -> list[dict]:
    with con() as c:
        rows = c.execute("SELECT role, text, ts FROM messages WHERE phone=? ORDER BY id DESC LIMIT ?", (phone, n)).fetchall()
    return [dict(r) for r in reversed(rows)]


def history(phone: str, n: int = 20) -> list[dict]:
    """Historial en formato Claude (une mensajes seguidos del mismo lado)."""
    out: list[dict] = []
    for r in last_messages(phone, n + 5):
        if r["role"] == "notice":
            continue
        role = "user" if r["role"] == "customer" else "assistant"
        text = {"human": "[Asesor humano] ", "handoff": "[Nota interna: ya se derivó a un asesor. Motivo: "}.get(
            r["role"], "") + r["text"] + ("]" if r["role"] == "handoff" else "")
        if out and out[-1]["role"] == role:
            out[-1]["content"] += "\n" + text
        else:
            out.append({"role": role, "content": text})
    while out and out[0]["role"] != "user":
        out.pop(0)
    return out


def contacts_where(field: str) -> list[str]:
    assert field in FIELDS
    with con() as c:
        return [r[0] for r in c.execute(f"SELECT phone FROM contacts WHERE {field}=1 ORDER BY phone")]


def stats() -> dict:
    day = int(time.time()) - 86400
    with con() as c:
        return {
            "contactos": c.execute("SELECT COUNT(*) FROM contacts").fetchone()[0],
            "pausados": c.execute("SELECT COUNT(*) FROM contacts WHERE paused=1").fetchone()[0],
            "mensajes_24h": c.execute("SELECT COUNT(*) FROM messages WHERE ts>=?", (day,)).fetchone()[0],
        }


def purge(days: int) -> int:
    cutoff = int(time.time()) - days * 86400
    with con() as c:
        removed = c.execute("DELETE FROM messages WHERE ts<?", (cutoff,)).rowcount
        c.execute(
            "DELETE FROM contacts WHERE first_seen<? "
            "AND NOT EXISTS (SELECT 1 FROM messages WHERE messages.phone=contacts.phone)",
            (cutoff,),
        )
        c.execute("DELETE FROM processed_webhook_messages WHERE received_at<?", (cutoff,))
        return removed
