"""Read-only SQL runner for the audit: python q.py "<sql>"  or  python q.py -f file.sql  (statements split by ';;')."""
import sys, time
import psycopg
from dotenv import dotenv_values
url = dotenv_values(r"C:\Users\Baloyi PA\Desktop\Niwis Backend\.env")["SUPABASE_DB_URL"]
args = sys.argv[1:]
if args and args[0] == "-f":
    sql_text = open(args[1], encoding="utf-8").read()
else:
    sql_text = " ".join(args)
import os
SD = os.path.dirname(os.path.abspath(__file__))
W = open(os.path.join(SD, "w.sql"), encoding="utf-8").read()
WMA = open(os.path.join(SD, "wma.sql"), encoding="utf-8").read()
stmts = [s.strip().replace("{W}", W).replace("{WMA}", WMA) for s in sql_text.split(";;") if s.strip()]
with psycopg.connect(url, autocommit=True) as conn:
    conn.execute("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY")
    conn.execute("SET statement_timeout = '300s'")
    for s in stmts:
        t = time.time()
        try:
            cur = conn.execute(s)
            rows = cur.fetchall() if cur.description else []
            cols = [d.name for d in cur.description] if cur.description else []
        except Exception as e:
            print("ERROR:", e); continue
        print("--", (s.strip().split("-- ")[-1].splitlines()[0] if "-- " in s[:3] else s.strip().splitlines()[0])[:120], f"[{len(rows)} rows, {time.time()-t:.1f}s]")
        if cols: print(" | ".join(cols))
        for r in rows[:400]:
            print(" | ".join("" if v is None else str(v) for v in r))
