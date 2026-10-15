"""The substitute replay API (a test fixture standing in for Component 1, Phase 4 runtime check only).

Serves what generate.py wrote in build/<app>/replay.json: GET /api/c2-replay/<id> runs that query's exact SQL
and answers {"rows": [...]}; GET /api/c2-replay/page/<route> answers one array per replayable query of the page.
Read-only: it connects as the SELECT-only user c2replay (tools/runtime/hms/initdb/02-replay-user.sql) and runs
nothing but the recorded SQL. Values are converted for JSON only (DECIMAL -> number, dates -> ISO text).

Usage, from component2_frontend/:
    venv/Scripts/python.exe tools/runtime/replay/server.py hms [--port 18090]
"""

import datetime
import decimal
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pymysql

HERE = Path(__file__).resolve().parent
DB = {"host": "127.0.0.1", "port": 13306, "user": "c2replay", "password": "c2replay", "database": "myhmsdb",
      "charset": "utf8mb4"}


def plain(value):
    if isinstance(value, decimal.Decimal):
        return float(value)
    if isinstance(value, (datetime.date, datetime.datetime, datetime.time)):
        return value.isoformat()
    if isinstance(value, datetime.timedelta):  # a TIME column comes back as a timedelta
        seconds = int(value.total_seconds())
        return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def rows(sql: str) -> list[dict]:
    connection = pymysql.connect(**DB, cursorclass=pymysql.cursors.DictCursor)
    try:
        with connection.cursor() as cursor:
            cursor.execute(sql)
            return [{k: plain(v) for k, v in row.items()} for row in cursor.fetchall()]
    finally:
        connection.close()


def handler(replay: dict):
    class Replay(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - the http.server name
            path = self.path.split("?", 1)[0].rstrip("/")
            if path in replay["queries"]:
                body = {"rows": rows(replay["queries"][path]["sql"])}
            elif path in replay["pages"]:
                body = {name: rows(replay["queries"][query]["sql"]) for name, query in replay["pages"][path].items()}
            else:
                self.send_error(404, "not a replay endpoint")
                return
            data = json.dumps(body).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):  # quiet: the capture's own log says what was fetched
            pass

    return Replay


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__.strip().splitlines()[-1], file=sys.stderr)
        return 2
    port = int(argv[argv.index("--port") + 1]) if "--port" in argv else 18090
    replay = json.loads((HERE / "build" / argv[0] / "replay.json").read_text(encoding="utf-8"))
    server = ThreadingHTTPServer(("127.0.0.1", port), handler(replay))
    print(f"replay API for {argv[0]} on http://127.0.0.1:{port} ({len(replay['queries'])} queries, "
          f"{len(replay['pages'])} pages)", flush=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
