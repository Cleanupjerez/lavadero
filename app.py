import os, hashlib, secrets
from datetime import datetime
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, session, jsonify, flash
from werkzeug.security import generate_password_hash, check_password_hash
import psycopg
from psycopg.rows import dict_row

app=Flask(__name__)
app.secret_key=os.environ.get("SECRET_KEY", secrets.token_hex(32))
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024
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
            cur.execute("""CREATE TABLE IF NOT EXISTS companies(
                id SERIAL PRIMARY KEY, name TEXT UNIQUE NOT NULL, active BOOLEAN NOT NULL DEFAULT TRUE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )""")
            cur.execute("""CREATE TABLE IF NOT EXISTS services(
                id SERIAL PRIMARY KEY, name TEXT UNIQUE NOT NULL, target_seconds INTEGER NOT NULL,
                active BOOLEAN NOT NULL DEFAULT TRUE, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )""")
            cur.execute("""CREATE TABLE IF NOT EXISTS cars(
                id SERIAL PRIMARY KEY, plate TEXT NOT NULL, service TEXT NOT NULL,
                worker_id INTEGER NOT NULL REFERENCES workers(id),
                started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), finished_at TIMESTAMPTZ,
                duration_seconds INTEGER, target_seconds INTEGER, notes TEXT, make TEXT, model TEXT, company TEXT, plate_photo BYTEA, plate_photo_mime TEXT
            )"""
            )
            for company in ["Particular", "Empresa 1", "Empresa 2", "Empresa 3"]:
                cur.execute("INSERT INTO companies(name) VALUES(%s) ON CONFLICT(name) DO NOTHING", (company,))
            # Servicios definitivos del lavadero. Los objetivos se mantienen también al actualizar
            # una instalación existente para que coincidan con la configuración acordada.
            for name, minutes in [("Lavado exterior",30),("Lavado interior + exterior",90),("Limpieza integral",150),("Repaso coche",15)]:
                cur.execute("""INSERT INTO services(name,target_seconds) VALUES(%s,%s)
                               ON CONFLICT(name) DO UPDATE SET target_seconds=EXCLUDED.target_seconds, active=TRUE""",
                            (name, minutes*60))
            # Ocultamos los nombres de servicio de ejemplo de versiones anteriores.
            cur.execute("UPDATE services SET active=FALSE WHERE name IN ('Lavado completo','Lavado interior','Premium')")
            # Compatibilidad con bases ya creadas en versiones anteriores.
            cur.execute("ALTER TABLE cars ADD COLUMN IF NOT EXISTS make TEXT")
            cur.execute("ALTER TABLE cars ADD COLUMN IF NOT EXISTS model TEXT")
            cur.execute("ALTER TABLE cars ADD COLUMN IF NOT EXISTS company TEXT")
            cur.execute("ALTER TABLE cars ADD COLUMN IF NOT EXISTS plate_photo BYTEA")
            cur.execute("ALTER TABLE cars ADD COLUMN IF NOT EXISTS plate_photo_mime TEXT")
            cur.execute("ALTER TABLE cars ADD COLUMN IF NOT EXISTS target_seconds INTEGER")
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
            # Coches que siguen en proceso.
            if u["role"]=="admin":
                cur.execute("""SELECT c.*,w.name worker_name FROM cars c JOIN workers w ON w.id=c.worker_id
                               WHERE c.finished_at IS NULL ORDER BY c.started_at""")
                active=cur.fetchall()
            else:
                cur.execute("""SELECT c.*,w.name worker_name FROM cars c JOIN workers w ON w.id=c.worker_id
                               WHERE c.finished_at IS NULL AND c.worker_id=%s ORDER BY c.started_at""",(u["id"],))
                active=cur.fetchall()
            if u["role"]=="admin":
                cur.execute("SELECT id,name FROM workers WHERE active=TRUE AND role='worker' ORDER BY name")
                workers=cur.fetchall()
            else:
                workers=[]
            cur.execute("SELECT id,name FROM companies WHERE active=TRUE ORDER BY name")
            companies=cur.fetchall()
            cur.execute("SELECT id,name,target_seconds FROM services WHERE active=TRUE ORDER BY name")
            services=cur.fetchall()

            # Resumen de los últimos 7 días para administración.
            if u["role"]=="admin":
                cur.execute("""SELECT
                    COUNT(*) AS cars,
                    COALESCE(ROUND(AVG(duration_seconds)/60.0,1),0) AS avg_min,
                    COALESCE(ROUND(100.0*AVG(CASE WHEN target_seconds>0 AND duration_seconds>0 THEN target_seconds::numeric/NULLIF(duration_seconds,0) ELSE NULL END),1),0) AS performance_pct,
                    COALESCE(ROUND(100.0*AVG(CASE WHEN target_seconds>0 AND duration_seconds>0 AND duration_seconds<=target_seconds THEN 1.0 ELSE 0.0 END),1),0) AS on_time_pct,
                    COALESCE(ROUND(100.0*AVG(CASE WHEN target_seconds>0 AND duration_seconds>0 AND duration_seconds>target_seconds THEN 1.0 ELSE 0.0 END),1),0) AS late_pct
                    FROM cars
                    WHERE finished_at IS NOT NULL AND finished_at >= CURRENT_DATE - INTERVAL '6 days'""")
            else:
                cur.execute("""SELECT
                    COUNT(*) AS cars,
                    COALESCE(ROUND(AVG(duration_seconds)/60.0,1),0) AS avg_min,
                    COALESCE(ROUND(100.0*AVG(CASE WHEN target_seconds>0 AND duration_seconds>0 THEN target_seconds::numeric/NULLIF(duration_seconds,0) ELSE NULL END),1),0) AS performance_pct,
                    COALESCE(ROUND(100.0*AVG(CASE WHEN target_seconds>0 AND duration_seconds>0 AND duration_seconds<=target_seconds THEN 1.0 ELSE 0.0 END),1),0) AS on_time_pct,
                    COALESCE(ROUND(100.0*AVG(CASE WHEN target_seconds>0 AND duration_seconds>0 AND duration_seconds>target_seconds THEN 1.0 ELSE 0.0 END),1),0) AS late_pct
                    FROM cars
                    WHERE finished_at IS NOT NULL AND worker_id=%s AND finished_at >= CURRENT_DATE - INTERVAL '6 days'""",(u["id"],))
            summary=cur.fetchone()
            # Para trabajadores: los días sin ningún coche NO cuentan como días trabajados.
            if u["role"]!="admin":
                cur.execute("""SELECT
                    COUNT(DISTINCT finished_at::date) AS worked_days,
                    COALESCE(ROUND(COUNT(*)::numeric / NULLIF(COUNT(DISTINCT finished_at::date),0),1),0) AS cars_per_worked_day
                    FROM cars
                    WHERE finished_at IS NOT NULL AND worker_id=%s
                      AND finished_at >= CURRENT_DATE - INTERVAL '6 days'""", (u["id"],))
                work_metrics=cur.fetchone()
                summary["worked_days"]=work_metrics["worked_days"] or 0
                summary["cars_per_worked_day"]=work_metrics["cars_per_worked_day"] or 0
            else:
                summary["worked_days"]=None
                summary["cars_per_worked_day"]=None

            if u["role"]=="admin":
                cur.execute("""SELECT d::date AS "day",
                    COUNT(c.id) cars,
                    COALESCE(ROUND(AVG(c.duration_seconds)/60.0,1),0) avg_min,
                    COALESCE(ROUND(100.0*AVG(CASE WHEN c.target_seconds>0 AND c.duration_seconds>0 THEN c.target_seconds::numeric/NULLIF(c.duration_seconds,0) ELSE NULL END),1),0) performance_pct
                    FROM generate_series(CURRENT_DATE - INTERVAL '6 days', CURRENT_DATE, INTERVAL '1 day') d
                    LEFT JOIN cars c ON c.finished_at::date=d::date AND c.finished_at IS NOT NULL
                    GROUP BY d::date ORDER BY "day" """)
                daily=cur.fetchall()
                cur.execute("""SELECT service,COUNT(*) cars,
                    COALESCE(ROUND(AVG(duration_seconds)/60.0,1),0) avg_min,
                    COALESCE(ROUND(AVG(target_seconds)/60.0,1),0) target_min
                    FROM cars WHERE finished_at IS NOT NULL AND finished_at >= CURRENT_DATE - INTERVAL '6 days'
                    GROUP BY service ORDER BY cars DESC, service LIMIT 6""")
                service_summary=cur.fetchall()
                cur.execute("""SELECT w.name,COUNT(c.id) cars,
                    COALESCE(ROUND(AVG(c.duration_seconds)/60.0,1),0) avg_min,
                    COALESCE(ROUND(100.0*AVG(CASE WHEN c.target_seconds>0 AND c.duration_seconds>0 THEN c.target_seconds::numeric/NULLIF(c.duration_seconds,0) ELSE NULL END),1),0) performance_pct
                    FROM workers w LEFT JOIN cars c ON c.worker_id=w.id AND c.finished_at IS NOT NULL AND c.finished_at >= CURRENT_DATE - INTERVAL '6 days'
                    WHERE w.role='worker' AND w.active=TRUE GROUP BY w.id ORDER BY performance_pct DESC,w.name LIMIT 6""")
                worker_summary=cur.fetchall()
            else:
                cur.execute("""SELECT d::date AS "day",
                    COUNT(c.id) cars,
                    COALESCE(ROUND(AVG(c.duration_seconds)/60.0,1),0) avg_min,
                    COALESCE(ROUND(100.0*AVG(CASE WHEN c.target_seconds>0 AND c.duration_seconds>0 THEN c.target_seconds::numeric/NULLIF(c.duration_seconds,0) ELSE NULL END),1),0) performance_pct
                    FROM generate_series(CURRENT_DATE - INTERVAL '6 days', CURRENT_DATE, INTERVAL '1 day') d
                    LEFT JOIN cars c ON c.finished_at::date=d::date AND c.worker_id=%s AND c.finished_at IS NOT NULL
                    GROUP BY d::date ORDER BY "day" """,(u["id"],))
                daily=cur.fetchall()
                cur.execute("""SELECT service,COUNT(*) cars,
                    COALESCE(ROUND(AVG(duration_seconds)/60.0,1),0) avg_min,
                    COALESCE(ROUND(AVG(target_seconds)/60.0,1),0) target_min
                    FROM cars WHERE finished_at IS NOT NULL AND worker_id=%s
                    AND finished_at >= CURRENT_DATE - INTERVAL '6 days'
                    GROUP BY service ORDER BY cars DESC, service LIMIT 6""",(u["id"],))
                service_summary=cur.fetchall()
                worker_summary=[]

    template = "dashboard.html" if u["role"]=="admin" else "worker.html"
    return render_template(template,user=u,active=active,workers=workers,companies=companies,services=services,
                           summary=summary,daily=daily,service_summary=service_summary,worker_summary=worker_summary)

@app.post("/cars/start")
@login_required
def start_car():
    u=current_user()
    plate=request.form.get("plate","").strip().upper()
    service=request.form.get("service","Lavado completo").strip()
    make=request.form.get("make","").strip()
    model=request.form.get("model","").strip()
    company=request.form.get("company","").strip()
    worker_id=u["id"] if u["role"]!="admin" else int(request.form.get("worker_id"))
    photo=request.files.get("plate_photo")
    photo_bytes=photo.read() if photo and photo.filename else None
    photo_mime=photo.mimetype if photo_bytes else None
    if not plate:
        flash("Introduce la matrícula.")
        return redirect(url_for("dashboard"))
    if not photo_bytes:
        flash("La foto del coche es obligatoria para iniciar el servicio.")
        return redirect(url_for("dashboard"))
    if not photo_mime or not photo_mime.startswith("image/"):
        flash("El archivo debe ser una imagen.")
        return redirect(url_for("dashboard"))
    with conn() as c:
        with c.cursor() as cur:
            cur.execute("SELECT target_seconds FROM services WHERE name=%s AND active=TRUE", (service,))
            service_row=cur.fetchone()
            if not service_row:
                flash("Selecciona un servicio válido.")
                return redirect(url_for("dashboard"))
            target_seconds=service_row["target_seconds"]
            cur.execute("""INSERT INTO cars(plate,service,worker_id,started_at,target_seconds,make,model,company,plate_photo,plate_photo_mime)
                           VALUES(%s,%s,%s,NOW(),%s,%s,%s,%s,%s,%s)""",
                        (plate,service,worker_id,target_seconds,make,model,company,photo_bytes,photo_mime))
    return redirect(url_for("dashboard"))

@app.get("/cars/<int:car_id>/plate-photo")
@login_required
def plate_photo(car_id):
    u=current_user()
    with conn() as c:
        with c.cursor() as cur:
            cur.execute("SELECT plate_photo,plate_photo_mime,worker_id FROM cars WHERE id=%s",(car_id,))
            car=cur.fetchone()
    if not car or not car["plate_photo"]:
        return "Foto no disponible",404
    if u["role"]!="admin" and car["worker_id"]!=u["id"]:
        return "No autorizado",403
    from flask import Response
    return Response(car["plate_photo"], mimetype=car["plate_photo_mime"] or "image/jpeg")

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
                               WHERE c.finished_at IS NOT NULL AND (%s='' OR c.plate ILIKE %s OR w.name ILIKE %s OR c.make ILIKE %s OR c.model ILIKE %s OR c.company ILIKE %s)
                               ORDER BY c.finished_at DESC LIMIT 300""",(q,f"%{q}%",f"%{q}%",f"%{q}%",f"%{q}%",f"%{q}%"))
            else:
                cur.execute("""SELECT c.*,w.name worker_name FROM cars c JOIN workers w ON w.id=c.worker_id
                               WHERE c.finished_at IS NOT NULL AND c.worker_id=%s AND
                               (%s='' OR c.plate ILIKE %s OR c.make ILIKE %s OR c.model ILIKE %s OR c.company ILIKE %s) ORDER BY c.finished_at DESC LIMIT 300""",
                            (u["id"],q,f"%{q}%",f"%{q}%",f"%{q}%",f"%{q}%"))
            rows=cur.fetchall()
    return render_template("history.html",user=u,rows=rows,q=q)

@app.route("/stats")
@admin_required
def stats():
    with conn() as c:
        with c.cursor() as cur:
            cur.execute("""SELECT w.id,w.name,COUNT(c.id) FILTER(WHERE c.finished_at IS NOT NULL) cars,
                    COALESCE(ROUND(AVG(c.duration_seconds) FILTER(WHERE c.finished_at IS NOT NULL)/60.0,1),0) avg_min,
                    COALESCE(ROUND(AVG(c.target_seconds) FILTER(WHERE c.finished_at IS NOT NULL)/60.0,1),0) target_min,
                    COALESCE(SUM(c.duration_seconds) FILTER(WHERE c.finished_at IS NOT NULL),0) total_sec,
                    COALESCE(ROUND(100.0*AVG(CASE WHEN c.finished_at IS NOT NULL AND c.target_seconds>0 THEN c.target_seconds::numeric/NULLIF(c.duration_seconds,0) ELSE NULL END),1),0) performance_pct,
                    COALESCE(ROUND(100.0*AVG(CASE WHEN c.finished_at IS NOT NULL AND c.target_seconds>0 AND c.duration_seconds<=c.target_seconds THEN 1.0 ELSE 0.0 END),1),0) on_time_pct
                    FROM workers w LEFT JOIN cars c ON c.worker_id=w.id
                    WHERE w.role='worker' AND w.active=TRUE GROUP BY w.id ORDER BY performance_pct DESC,w.name""")
            rows=cur.fetchall()
            cur.execute("""SELECT service,COUNT(*) cars,
                    COALESCE(ROUND(AVG(duration_seconds)/60.0,1),0) avg_min,
                    COALESCE(ROUND(AVG(target_seconds)/60.0,1),0) target_min,
                    COALESCE(ROUND(100.0*AVG(CASE WHEN target_seconds>0 THEN target_seconds::numeric/NULLIF(duration_seconds,0) ELSE NULL END),1),0) performance_pct,
                    COALESCE(ROUND(100.0*AVG(CASE WHEN target_seconds>0 AND duration_seconds<=target_seconds THEN 1.0 ELSE 0.0 END),1),0) on_time_pct
                    FROM cars WHERE finished_at IS NOT NULL GROUP BY service ORDER BY service""")
            service_rows=cur.fetchall()
    return render_template("stats.html",user=current_user(),rows=rows,service_rows=service_rows)

@app.route("/services",methods=["GET","POST"])
@admin_required
def services():
    if request.method=="POST":
        name=request.form.get("name","").strip()
        minutes=int(request.form.get("minutes",0) or 0)
        if name and minutes>0:
            with conn() as c:
                with c.cursor() as cur:
                    cur.execute("INSERT INTO services(name,target_seconds) VALUES(%s,%s) ON CONFLICT(name) DO UPDATE SET target_seconds=EXCLUDED.target_seconds, active=TRUE",(name,minutes*60))
        return redirect(url_for("services"))
    with conn() as c:
        with c.cursor() as cur:
            cur.execute("SELECT id,name,target_seconds,active FROM services ORDER BY active DESC,name")
            rows=cur.fetchall()
    return render_template("services.html",user=current_user(),rows=rows)

@app.post("/services/<int:service_id>/toggle")
@admin_required
def toggle_service(service_id):
    with conn() as c:
        with c.cursor() as cur:
            cur.execute("UPDATE services SET active=NOT active WHERE id=%s",(service_id,))
    return redirect(url_for("services"))

@app.route("/companies",methods=["GET","POST"])
@admin_required
def companies():
    if request.method=="POST":
        name=request.form.get("name","").strip()
        if name:
            with conn() as c:
                with c.cursor() as cur:
                    try:
                        cur.execute("INSERT INTO companies(name) VALUES(%s) ON CONFLICT(name) DO NOTHING",(name,))
                    except Exception:
                        c.rollback()
        return redirect(url_for("companies"))
    with conn() as c:
        with c.cursor() as cur:
            cur.execute("SELECT id,name,active FROM companies ORDER BY active DESC,name")
            rows=cur.fetchall()
    return render_template("companies.html",user=current_user(),rows=rows)

@app.post("/companies/<int:company_id>/toggle")
@admin_required
def toggle_company(company_id):
    with conn() as c:
        with c.cursor() as cur:
            cur.execute("UPDATE companies SET active=NOT active WHERE id=%s",(company_id,))
    return redirect(url_for("companies"))

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
