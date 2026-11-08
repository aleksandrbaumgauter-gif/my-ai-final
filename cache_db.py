import sqlite3, time, hashlib

class Cache:
    def __init__(self, ttl=3600):
        self.conn = sqlite3.connect("cache.db", check_same_thread=False)
        self.conn.execute("CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY, val TEXT, time INTEGER, provider TEXT)")
        self.conn.execute("DELETE FROM cache WHERE time < ?", (int(time.time()) - 86400,))
        self.conn.commit()
        self.ttl = ttl

    def get(self, prompt, skip=False):
        if skip: return None, None
        key = hashlib.sha256(prompt.lower().strip().encode()).hexdigest()
        row = self.conn.execute("SELECT val, provider FROM cache WHERE key = ? AND time > ?", (key, int(time.time()) - self.ttl)).fetchone()
        return (row[0], row[1]) if row else (None, None)

    def set(self, prompt, val, provider="Unknown"):
        if "Ошибка" in val or "error" in val.lower(): return
        key = hashlib.sha256(prompt.lower().strip().encode()).hexdigest()
        self.conn.execute("INSERT OR REPLACE INTO cache VALUES (?, ?, ?, ?)", (key, val, int(time.time()), provider))
        self.conn.commit()

    def clear(self):
        self.conn.execute("DELETE FROM cache")
        self.conn.commit()
        return self.conn.total_changes

    def stats(self):
        row = self.conn.execute("SELECT COUNT(*), SUM(LENGTH(val)) FROM cache").fetchone()
        return {"records": row[0] or 0, "size_bytes": row[1] or 0}

cache = Cache()