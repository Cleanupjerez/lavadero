import os
from datetime import datetime, timedelta
from functools import wraps

import psycopg
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-me")
DATABASE_URL = os.environ.get("DATABASE_URL")

SERVICES = {
    "Lavado exterior": 30,
    "Lavado interior + exterior": 90,
    "Limpieza integral": 150,
    "Repaso": 15,
}
COMPANIES = [
    "Citroën", "Kia VO", "Kia VN", "Jerez Motor", "Toyota",
    "Jesús Compraventa", "Lapie", "C2U", "Crestanevada"
]
WORKERS = ["Carlos", "Silvia", "Miriam", "Cesar", "Jorge", "Paulo"]

CHECKLIST = [
    "Llantas", "Mosquitos", "Pasos de rueda", "Tapón gasolina", "Chapa",
    "Motor", "Cristales", "Plásticos exteriores", "Cantos", "Bandeja maletero",
    "Alfombras", "Hueco rueda repuesto", "Guantera", "Isofix",
    "Palanca subir asientos", "Rejillas ventilación", "Pantalla navegador",
    "Pedales", "Railes", "Espejos int y ext", "Parte abajo cinturón",
    "Bolsa detrás asientos", "Quitar ambientadores"
]

def db():
    return psycopg.connect(DATABASE_URL)

def init_db():
    if not DATABASE_URL:
        return
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id SERIAL PRIMARY KEY,
                    username TEXT UNIQUE NOT NULL,
                    name TEXT NOT NULL,
                    role TEXT NOT NULL DEFAULT 'worker',
                    password_hash TEXT NOT NULL,
                    must_change_password BOOLEAN NOT NULL DEFAULT TRUE,
                    active BOOLEAN NOT NULL DEFAULT TRUE
                );
                CREATE TABLE IF NOT EXISTS cars (
                    id SERIAL PRIMARY KEY,
                    plate TEXT NOT NULL,
                    brand TEXT,
                    model TEXT,
                    company TEXT NOT NULL,
                    service TEXT NOT NULL,
                    target_minutes INTEGER NOT NULL,
                    worker_id INTEGER REFERENCES users(id),
                    photo_path TEXT,
                    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    finished_at TIMESTAMPTZ,
                    actual_minutes NUMERIC,
                    checklist_status TEXT NOT NULL DEFAULT 'pending'
                );
                CREATE TABLE IF NOT EXISTS checklist_items (
                    id SERIAL PRIMARY KEY,
                    car_id INTEGER REFERENCES cars(id) ON DELETE CASCADE,
                    item TEXT NOT NULL,
                    ok BOOLEAN NOT NULL DEFAULT FALSE,
                    reviewed BOOLEAN NOT NULL DEFAULT FALSE,
                    UNIQUE(car_id, item)
                );
                CREATE TABLE IF NOT EXISTS checklist_notes (
                    id SERIAL PRIMARY KEY,
                    car_id INTEGER REFERENCES cars(id) ON DELETE CASCADE,
                    notes TEXT,
                    photo_path TEXT,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
            """)
            defaults = [("admin", "Administrador", "admin", "admin123")]
            defaults += [(n.lower(), n, "worker", "1234") for n in WORKERS]
            for username, name, role, password in defaults:
                cur.execute("""
                    INSERT INTO users(username,name,role,password_hash,must_change_password)
                    VALUES (%s,%s,%s,%s,TRUE)
                    ON CONFLICT (username) DO NOTHING
                """, (username, name, role, generate_password_hash(password)))
        conn.commit()

def current_user():
    uid = session.get("user_id")
    if not uid or not DATABASE_URL:
        return None
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id,username,name,role,must_change_password FROM users WHERE id=%s AND active=TRUE", (uid,))
            row = cur.fetchone()
            return row

def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        u = current_user()
        if not u:
            return redirect(url_for("login"))
        if u[4] and request.endpoint != "change_password":
            return redirect(url_for("change_password"))
        return view(*args, **kwargs)
    return wrapped

def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        u = current_user()
        if not u or u[3] != "admin":
            return redirect(url_for("home"))
        if u[4]:
            return redirect(url_for("change_password"))
        return view(*args, **kwargs)
    return wrapped

@app.context_processor
def inject():
    return {"user": current_user(), "services": SERVICES, "companies": COMPANIES}

@app.route("/")
@login_required
def home():
    u = current_user()
    if u[3] == "admin":
        return redirect(url_for("admin_dashboard"))
    return redirect(url_for("worker_dashboard"))

@app.route("/login", methods=["GET","POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username","").strip().lower()
        password = request.form.get("password","")
        with db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id,password_hash FROM users WHERE username=%s AND active=TRUE", (username,))
                row = cur.fetchone()
        if row and check_password_hash(row[1], password):
            session["user_id"] = row[0]
            return redirect(url_for("home"))
        flash("Usuario o contraseña incorrectos.", "error")
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.route("/change-password", methods=["GET","POST"])
@login_required
def change_password():
    u = current_user()
    if request.method == "POST":
        current = request.form.get("current_password","")
        new = request.form.get("new_password","")
        confirm = request.form.get("confirm_password","")
        if len(new) < 4:
            flash("La nueva contraseña debe tener al menos 4 caracteres.", "error")
        elif new != confirm:
            flash("Las contraseñas nuevas no coinciden.", "error")
        else:
            with db() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT password_hash FROM users WHERE id=%s", (u[0],))
                    old = cur.fetchone()
                    if not old or not check_password_hash(old[0], current):
                        flash("La contraseña actual no es correcta.", "error")
                    else:
                        cur.execute("UPDATE users SET password_hash=%s,must_change_password=FALSE WHERE id=%s",
                                    (generate_password_hash(new), u[0]))
                        conn.commit()
                        return redirect(url_for("home"))
    return render_template("change_password.html")

@app.route("/worker")
@login_required
def worker_dashboard():
    u = current_user()
    if u[3] == "admin":
        return redirect(url_for("admin_dashboard"))
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id,plate,brand,model,company,service,target_minutes,started_at
                FROM cars WHERE worker_id=%s AND finished_at IS NULL ORDER BY started_at DESC LIMIT 1
            """, (u[0],))
            active = cur.fetchone()
            cur.execute("""
                SELECT COUNT(*), COALESCE(SUM(actual_minutes),0), COALESCE(AVG(actual_minutes),0)
                FROM cars WHERE worker_id=%s AND finished_at >= NOW() - INTERVAL '7 days'
            """, (u[0],))
            stats = cur.fetchone()
    return render_template("worker.html", active=active, stats=stats, checklist=CHECKLIST)

@app.route("/start", methods=["POST"])
@login_required
def start_car():
    u = current_user()
    if u[3] == "admin":
        return redirect(url_for("admin_dashboard"))
    plate = request.form.get("plate","").strip().upper()
    brand = request.form.get("brand","").strip()
    model = request.form.get("model","").strip()
    company = request.form.get("company","")
    service = request.form.get("service","")
    if not plate or company not in COMPANIES or service not in SERVICES:
        flash("Completa matrícula, empresa y servicio.", "error")
        return redirect(url_for("worker_dashboard"))
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM cars WHERE worker_id=%s AND finished_at IS NULL LIMIT 1", (u[0],))
            if cur.fetchone():
                flash("Ya tienes un coche en proceso.", "error")
                return redirect(url_for("worker_dashboard"))
            cur.execute("""
                INSERT INTO cars(plate,brand,model,company,service,target_minutes,worker_id,checklist_status)
                VALUES (%s,%s,%s,%s,%s,%s,%s,'pending') RETURNING id
            """, (plate,brand,model,company,service,SERVICES[service],u[0]))
            car_id = cur.fetchone()[0]
            if service == "Repaso":
                for item in CHECKLIST:
                    cur.execute("INSERT INTO checklist_items(car_id,item) VALUES (%s,%s)", (car_id,item))
        conn.commit()
    return redirect(url_for("worker_dashboard"))

@app.route("/finish/<int:car_id>", methods=["POST"])
@login_required
def finish_car(car_id):
    u = current_user()
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT service,started_at FROM cars WHERE id=%s AND worker_id=%s AND finished_at IS NULL",
                        (car_id,u[0]))
            row = cur.fetchone()
            if not row:
                return redirect(url_for("worker_dashboard"))
            if row[0] == "Repaso":
                cur.execute("SELECT COUNT(*) FROM checklist_items WHERE car_id=%s AND (NOT ok AND NOT reviewed)", (car_id,))
                if cur.fetchone()[0] > 0:
                    flash("Completa todos los puntos del checklist antes de finalizar.", "error")
                    return redirect(url_for("worker_dashboard"))
                cur.execute("UPDATE cars SET checklist_status='done' WHERE id=%s", (car_id,))
            cur.execute("""
                UPDATE cars SET finished_at=NOW(),
                actual_minutes=EXTRACT(EPOCH FROM (NOW()-started_at))/60
                WHERE id=%s
            """, (car_id,))
        conn.commit()
    return redirect(url_for("worker_dashboard"))

@app.route("/checklist/<int:car_id>", methods=["POST"])
@login_required
def save_checklist(car_id):
    u = current_user()
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM cars WHERE id=%s AND worker_id=%s AND service='Repaso' AND finished_at IS NULL", (car_id,u[0]))
            if not cur.fetchone():
                return redirect(url_for("worker_dashboard"))
            for i, item in enumerate(CHECKLIST):
                ok = request.form.get(f"ok_{i}") == "1"
                reviewed = request.form.get(f"reviewed_{i}") == "1"
                cur.execute("UPDATE checklist_items SET ok=%s, reviewed=%s WHERE car_id=%s AND item=%s",
                            (ok,reviewed,car_id,item))
        conn.commit()
    flash("Checklist guardado.", "success")
    return redirect(url_for("worker_dashboard"))

@app.route("/admin")
@admin_required
def admin_dashboard():
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT COUNT(*), COALESCE(AVG(actual_minutes),0),
                COALESCE(AVG(CASE WHEN actual_minutes <= target_minutes THEN 100.0
                    ELSE target_minutes*100.0/NULLIF(actual_minutes,0) END),0)
                FROM cars WHERE finished_at >= NOW() - INTERVAL '7 days'
            """)
            summary = cur.fetchone()
            cur.execute("""
                SELECT COUNT(*) FROM cars WHERE finished_at IS NULL
            """)
            active_count = cur.fetchone()[0]
            cur.execute("""
                SELECT COUNT(*) FROM cars
                WHERE finished_at >= NOW() - INTERVAL '7 days' AND checklist_status='pending'
            """)
            pending = cur.fetchone()[0]
            cur.execute("""
                SELECT u.name, COUNT(c.id), COUNT(DISTINCT c.finished_at::date),
                       COALESCE(AVG(c.actual_minutes),0)
                FROM users u LEFT JOIN cars c ON c.worker_id=u.id
                AND c.finished_at >= NOW() - INTERVAL '7 days'
                WHERE u.role='worker' AND u.active=TRUE
                GROUP BY u.id,u.name ORDER BY u.id
            """)
            workers = cur.fetchall()
    return render_template("admin.html", summary=summary, active_count=active_count, pending=pending, workers=workers)

@app.route("/admin/cars")
@admin_required
def admin_cars():
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT c.id,c.plate,c.brand,c.model,c.company,c.service,u.name,
                       c.actual_minutes,c.target_minutes,c.finished_at,c.checklist_status
                FROM cars c JOIN users u ON u.id=c.worker_id
                ORDER BY c.started_at DESC LIMIT 300
            """)
            cars = cur.fetchall()
    return render_template("cars.html", cars=cars)

@app.route("/admin/checklists")
@admin_required
def admin_checklists():
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT c.id,c.plate,c.brand,c.model,c.company,c.service,u.name,c.started_at,c.checklist_status
                FROM cars c JOIN users u ON u.id=c.worker_id
                WHERE c.checklist_status='pending'
                ORDER BY c.started_at DESC
            """)
            pending = cur.fetchall()
    return render_template("checklists.html", pending=pending)

@app.route("/admin/workers")
@admin_required
def admin_workers():
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id,username,name,active,must_change_password FROM users WHERE role='worker' ORDER BY id")
            workers = cur.fetchall()
    return render_template("workers.html", workers=workers)

@app.route("/admin/reset/<int:user_id>", methods=["POST"])
@admin_required
def reset_password(user_id):
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE users SET password_hash=%s,must_change_password=TRUE
                WHERE id=%s AND role='worker'
            """, (generate_password_hash("1234"), user_id))
        conn.commit()
    return redirect(url_for("admin_workers"))

@app.route("/health")
def health():
    return "OK", 200

with app.app_context():
    init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT","8080")))
