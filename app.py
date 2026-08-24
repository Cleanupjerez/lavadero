import os, hashlib, secrets
from datetime import datetime
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, session, jsonify, flash
from werkzeug.security import generate_password_hash, check_password_hash
import psycopg
from psycopg.rows import dict_row

app=Flask(__name__)
app.secret_key=os.environ.get("SECRET_KEY", secrets.token_hex(32))
DATABASE_URL=os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("Falta DATABASE_URL. En Railway debe estar conectada la base PostgreSQL.")

def conn():
   return psycopg.connect(DATABASE_URL, row_factory=dict_row)

def init_db():
    with conn() as c:
        with c.cursor() as cur:
            cur.execute("""CREATE TABLE IF NOT EXISTS workers(
                id SERIAL PRIMARY KEY, username TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
                password_hash TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'worker',
                active BOOLEAN NOT NULL DEFAULT TRUE, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )""")
            cur.execute("""CREATE TABLE IF NOT EXISTS cars(
                id SERIAL PRIMARY KEY, plate TEXT NOT NULL, service TEXT NOT NULL,
                worker_id INTEGER NOT NULL REFERENCES workers(id),
                started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), finished_at TIMESTAMPTZ,
                duration_seconds INTEGER, notes TEXT
            )""")
            users=[
                ("admin","Administrador","admin","1234"),("juan","Juan","worker","1234"),
                ("pedro","Pedro","worker","1234"),("antonio","Antonio","worker","1234"),
                ("manuel","Manuel","worker","1234"),("carlos","Carlos","worker","1234"),
                ("david","David","worker","1234"),("miguel","Miguel","worker","1234"),
                ("jose","José","worker","1234"),("luis","Luis","worker","1234")]
            for u,n,r,p in users:
                cur.execute("""INSERT INTO workers(username,name,role,password_hash)
                               VALUES(%s,%s,%s,%s) ON CONFLICT(username) DO NOTHING""",
                            (u,n,r,generate_password_hash(p)))

def current_user():
    uid=session.get("user_id")
    if not uid:return None
    with conn() as c:
        with c.cursor() as cur:
            cur.execute("SELECT * FROM workers WHERE id=%s AND active=TRUE",(uid,))
            return cur.fetchone()

def login_required(fn):
    @wraps(fn)
    def wrapper(*a,**kw):
        if not current_user(): return redirect(url_for("login"))
        return fn(*a,**kw)
    return wrapper

def admin_required(fn):
    @wraps(fn)
    def wrapper(*a,**kw):
        u=current_user()
        if not u:return redirect(url_for("login"))
        if u["role"]!="admin": return redirect(url_for("dashboard"))
        return fn(*a,**kw)
    return wrapper

@app.route("/health")
def health(): return "OK",200

@app.route("/login",methods=["GET","POST"])
def login():
    if request.method=="POST":
        username=request.form.get("username","").strip().lower()
        password=request.form.get("password","")
        with conn() as c:
            with c.cursor() as cur:
                cur.execute("SELECT * FROM workers WHERE username=%s AND active=TRUE",(username,))
                u=cur.fetchone()
        if u and check_password_hash(u["password_hash"],password):
            session.clear();session["user_id"]=u["id"]
            return redirect(url_for("dashboard"))
        flash("Usuario o contraseña incorrectos.")
    return render_template("login.html")

@app.route("/logout")
def logout(): session.clear(); return redirect(url_for("login"))

@app.route("/")
@login_required
def dashboard():
    u=current_user()
    with conn() as c:
        with c.cursor() as cur:
            if u["role"]=="admin":
                cur.execute("""SELECT c.*,w.name worker_name FROM cars c JOIN workers w ON w.id=c.worker_id
                               WHERE c.finished_at IS NULL ORDER BY c.started_at""")
                active=cur.fetchall()
                cur.execute("SELECT COUNT(*) n FROM cars WHERE finished_at::date=CURRENT_DATE")
                completed=cur.fetchone()["n"]
            else:
                cur.execute("""SELECT c.*,w.name worker_name FROM cars c JOIN workers w ON w.id=c.worker_id
                               WHERE c.finished_at IS NULL AND c.worker_id=%s ORDER BY c.started_at""",(u["id"],))
                active=cur.fetchall()
                cur.execute("SELECT COUNT(*) n FROM cars WHERE worker_id=%s AND finished_at::date=CURRENT_DATE",(u["id"],))
                completed=cur.fetchone()["n"]
            cur.execute("SELECT id,name FROM workers WHERE active=TRUE AND role='worker' ORDER BY name")
            workers=cur.fetchall()
    return render_template("dashboard.html",user=u,active=active,completed=completed,workers=workers)

@app.post("/cars/start")
@login_required
def start_car():
    u=current_user()
    plate=request.form.get("plate","").strip().upper()
    service=request.form.get("service","Lavado completo").strip()
    worker_id=u["id"] if u["role"]!="admin" else int(request.form.get("worker_id"))
    if not plate: flash("Introduce la matrícula."); return redirect(url_for("dashboard"))
    with conn() as c:
        with c.cursor() as cur:
            cur.execute("""INSERT INTO cars(plate,service,worker_id,started_at)
                           VALUES(%s,%s,%s,NOW())""",(plate,service,worker_id))
    return redirect(url_for("dashboard"))

@app.post("/cars/<int:car_id>/finish")
@login_required
def finish_car(car_id):
    u=current_user()
    with conn() as c:
        with c.cursor() as cur:
            cur.execute("SELECT * FROM cars WHERE id=%s",(car_id,)); car=cur.fetchone()
            if not car or car["finished_at"]: return redirect(url_for("dashboard"))
            if u["role"]!="admin" and car["worker_id"]!=u["id"]: return redirect(url_for("dashboard"))
            cur.execute("""UPDATE cars SET finished_at=NOW(),
                         duration_seconds=GREATEST(0,EXTRACT(EPOCH FROM (NOW()-started_at))::integer)
                         WHERE id=%s""",(car_id,))
    return redirect(url_for("dashboard"))

@app.route("/history")
@login_required
def history():
    u=current_user()
    q=request.args.get("q","").strip()
    with conn() as c:
        with c.cursor() as cur:
            if u["role"]=="admin":
                cur.execute("""SELECT c.*,w.name worker_name FROM cars c JOIN workers w ON w.id=c.worker_id
                               WHERE c.finished_at IS NOT NULL AND (%s='' OR c.plate ILIKE %s OR w.name ILIKE %s)
                               ORDER BY c.finished_at DESC LIMIT 300""",(q,f"%{q}%",f"%{q}%"))
            else:
                cur.execute("""SELECT c.*,w.name worker_name FROM cars c JOIN workers w ON w.id=c.worker_id
                               WHERE c.finished_at IS NOT NULL AND c.worker_id=%s AND
                               (%s='' OR c.plate ILIKE %s) ORDER BY c.finished_at DESC LIMIT 300""",
                            (u["id"],q,f"%{q}%"))
            rows=cur.fetchall()
    return render_template("history.html",user=u,rows=rows,q=q)

@app.route("/stats")
@admin_required
def stats():
    with conn() as c:
        with c.cursor() as cur:
            cur.execute("""SELECT w.id,w.name,COUNT(c.id) FILTER(WHERE c.finished_at IS NOT NULL) cars,
                    COALESCE(ROUND(AVG(c.duration_seconds) FILTER(WHERE c.finished_at IS NOT NULL)/60.0,1),0) avg_min,
                    COALESCE(SUM(c.duration_seconds) FILTER(WHERE c.finished_at IS NOT NULL),0) total_sec
                    FROM workers w LEFT JOIN cars c ON c.worker_id=w.id
                    WHERE w.role='worker' AND w.active=TRUE GROUP BY w.id ORDER BY cars DESC,w.name""")
            rows=cur.fetchall()
    return render_template("stats.html",user=current_user(),rows=rows)

@app.route("/workers",methods=["GET","POST"])
@admin_required
def workers():
    if request.method=="POST":
        name=request.form["name"].strip(); username=request.form["username"].strip().lower(); password=request.form["password"]
        with conn() as c:
            with c.cursor() as cur:
                cur.execute("SELECT COUNT(*) n FROM workers WHERE role='worker' AND active=TRUE")
                if cur.fetchone()["n"]>=10: flash("Máximo de 10 trabajadores activos."); return redirect(url_for("workers"))
                try:
                    cur.execute("INSERT INTO workers(name,username,password_hash) VALUES(%s,%s,%s)",
                                (name,username,generate_password_hash(password)))
                except Exception:
                    c.rollback(); flash("Ese usuario ya existe o los datos no son válidos.")
        return redirect(url_for("workers"))
    with conn() as c:
        with c.cursor() as cur:
            cur.execute("SELECT id,name,username,active FROM workers WHERE role='worker' ORDER BY name")
            rows=cur.fetchall()
    return render_template("workers.html",user=current_user(),rows=rows)

@app.post("/workers/<int:worker_id>/toggle")
@admin_required
def toggle_worker(worker_id):
    with conn() as c:
        with c.cursor() as cur:
            cur.execute("UPDATE workers SET active=NOT active WHERE id=%s AND role='worker'",(worker_id,))
    return redirect(url_for("workers"))

@app.route("/admin")
@admin_required
def admin(): return redirect(url_for("stats"))

with app.app_context():
    init_db()

if __name__=="__main__":
    app.run(host="0.0.0.0",port=int(os.environ.get("PORT",5000)))
