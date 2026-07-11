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
            # keep up to 30 days (720 hours, ~259k points)
            cutoff = ts - (720 * 3600)
            conn.execute('DELETE FROM history WHERE ts < ?', (cutoff,))
            conn.commit()

def get_history(hours: int = 1) -> List[Dict]:
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
