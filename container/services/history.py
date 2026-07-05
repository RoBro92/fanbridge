import sqlite3
import time
import os
import threading
from typing import List, Dict

_DB_PATH = "/config/history.db"
if not os.path.exists("/config"):
    _DB_PATH = "history.db" # local dev fallback

_lock = threading.Lock()

def _get_db():
    conn = sqlite3.connect(_DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with _lock:
        with _get_db() as conn:
            conn.execute('''
                CREATE TABLE IF NOT EXISTS history (
                    ts INTEGER PRIMARY KEY,
                    hdd_avg INTEGER,
                    ssd_avg INTEGER,
                    pwm INTEGER
                )
            ''')
            conn.commit()

def record_status(hdd_avg: int, ssd_avg: int, pwm: int):
    ts = int(time.time())
    with _lock:
        with _get_db() as conn:
            conn.execute('INSERT OR REPLACE INTO history (ts, hdd_avg, ssd_avg, pwm) VALUES (?, ?, ?, ?)',
                         (ts, hdd_avg, ssd_avg, pwm))
            # keep only last 24 hours (assuming 1 data point per 10s -> 8640 points)
            cutoff = ts - (24 * 3600)
            conn.execute('DELETE FROM history WHERE ts < ?', (cutoff,))
            conn.commit()

def get_history(hours: int = 1) -> List[Dict]:
    cutoff = int(time.time()) - (hours * 3600)
    with _lock:
        with _get_db() as conn:
            cur = conn.execute('SELECT ts, hdd_avg, ssd_avg, pwm FROM history WHERE ts >= ? ORDER BY ts ASC', (cutoff,))
            return [dict(row) for row in cur.fetchall()]

init_db()
