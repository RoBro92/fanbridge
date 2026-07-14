import sqlite3
import time
import os
import threading
from pathlib import Path
from typing import List, Dict, Optional

_DB_PATH = os.environ.get("FANBRIDGE_HISTORY_DB", "").strip()
if not _DB_PATH:
    _DB_PATH = "/config/history.db" if os.path.isdir("/config") else str(
        Path(__file__).resolve().parents[1] / "history.local.db"
    )

_lock = threading.Lock()

def _get_db():
    Path(_DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(_DB_PATH, check_same_thread=False, timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")
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
        os.chmod(_DB_PATH, 0o600)

def record_status(hdd_avg: Optional[int], ssd_avg: Optional[int], pwm: int):
    ts = int(time.time())
    with _lock:
        with _get_db() as conn:
            conn.execute('INSERT OR REPLACE INTO history (ts, hdd_avg, ssd_avg, pwm) VALUES (?, ?, ?, ?)',
                         (ts, hdd_avg, ssd_avg, pwm))
            # keep up to 30 days (720 hours, ~259k points)
            cutoff = ts - (720 * 3600)
            conn.execute('DELETE FROM history WHERE ts < ?', (cutoff,))
            conn.commit()

def get_history(hours: int = 1) -> List[Dict]:
    hours = max(1, min(720, int(hours)))
    cutoff = int(time.time()) - (hours * 3600)
    
    if hours <= 1:
        bucket = 10
    elif hours <= 12:
        bucket = 120 # 2 mins
    elif hours <= 24:
        bucket = 300 # 5 mins
    elif hours <= 168:
        bucket = 1800 # 30 mins
    else:
        bucket = 7200 # 2 hours
        
    with _lock:
        with _get_db() as conn:
            if bucket <= 10:
                cur = conn.execute('SELECT ts, hdd_avg, ssd_avg, pwm FROM history WHERE ts >= ? ORDER BY ts ASC', (cutoff,))
            else:
                cur = conn.execute('''
                    SELECT 
                        (ts / ?) * ? as ts,
                        CAST(ROUND(AVG(hdd_avg)) AS INTEGER) as hdd_avg,
                        CAST(ROUND(AVG(ssd_avg)) AS INTEGER) as ssd_avg,
                        CAST(ROUND(AVG(pwm)) AS INTEGER) as pwm
                    FROM history 
                    WHERE ts >= ? 
                    GROUP BY (ts / ?)
                    ORDER BY ts ASC
                ''', (bucket, bucket, cutoff, bucket))
            return [dict(row) for row in cur.fetchall()]

init_db()
