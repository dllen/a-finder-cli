import sqlite3

from flask import Flask, jsonify, request


def open_conn(db_path):
    return sqlite3.connect(db_path)


def list_dates(conn):
    rows = conn.execute("SELECT DISTINCT date FROM daily_picks ORDER BY date DESC").fetchall()
    return [r[0] for r in rows]


def picks_for_date(conn, date):
    rows = conn.execute(
        "SELECT rank, kind, code, name, strategy, buy, stop, target, score FROM daily_picks WHERE date = ? ORDER BY kind, rank",
        (date,),
    ).fetchall()
    return [
        {"rank": r[0], "kind": r[1], "code": r[2], "name": r[3], "strategy": r[4],
         "buy": r[5], "stop": r[6], "target": r[7], "score": r[8]}
        for r in rows
    ]


def create_app(db_path="hs300.db", top=10):
    app = Flask(__name__)

    @app.get("/api/dates")
    def dates():
        conn = open_conn(db_path)
        ds = list_dates(conn)
        conn.close()
        return jsonify({"dates": ds})

    @app.get("/api/picks")
    def picks():
        date = request.args.get("date", "")
        conn = open_conn(db_path)
        if not date:
            ds = list_dates(conn)
            date = ds[0] if ds else ""
        groups = {}
        if date:
            for row in picks_for_date(conn, date):
                groups.setdefault(row["kind"], []).append(row)
        conn.close()
        return jsonify({"date": date, "groups": groups})

    return app
