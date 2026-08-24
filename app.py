import os, uuid, requests
from functools import wraps
import psycopg
from flask import Flask, render_template, request, redirect, url_for, session, flash, send_from_directory
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

app=Flask(__name__)
app.secret_key=os.environ.get("SECRET_KEY","change-this-secret")
DATABASE_URL=os.environ.get("DATABASE_URL")
CONTASIMPLE_SITE_URL=os.environ.get("CONTASIMPLE_SITE_URL","")
CONTASIMPLE_API_URL=os.environ.get("CONTASIMPLE_API_URL","")
CONTASIMPLE_CLIENT_ID=os.environ.get("CONTASIMPLE_CLIENT_ID","")
CONTASIMPLE_CLIENT_SECRET=os.environ.get("CONTASIMPLE_CLIENT_SECRET","")
CONTASIMPLE_REDIRECT_URI=os.environ.get("CONTASIMPLE_REDIRECT_URI","")
CONTASIMPLE_ALBARAN_ENDPOINT=os.environ.get("CONTASIMPLE_ALBARAN_ENDPOINT","")
UPLOAD_DIR=os.path.join(os.path.dirname(__file__),"uploads")
os.makedirs(UPLOAD_DIR,exist_ok=True)

COMPANIES=["Citroën","Kia VO","Kia VN","Jerez Motor","Toyota","Jesús Compraventa","Lapie","C2U","Crestanevada"]
SERVICES={"Lavado exterior":30,"Lavado interior + exterior":90,"Limpieza integral":150,"Repaso":15}
WORKERS=["Carlos","Silvia","Miriam","Cesar","Jorge","Paulo"]
CHECKLIST=["Llantas","Mosquitos","Pasos de rueda","Tapón gasolina","Chapa","Motor","Cristales","Plásticos exteriores","Cantos","Bandeja maletero","Alfombras","Hueco rueda repuesto","Guantera","Isofix","Palanca subir asientos","Rejillas ventilación","Pantalla navegador","Pedales","Railes","Espejos int y ext","Parte abajo cinturón","Bolsa detrás asientos","Quitar ambientadores"]

def db(): return psycopg.connect(DATABASE_URL)
def col(c,t,n,d): c.execute(f"ALTER TABLE {t} ADD COLUMN IF NOT EXISTS {n} {d}")

def init_db():
    if not DATABASE_URL: return
    with db() as conn:
        with conn.cursor() as c:
            c.execute("""CREATE TABLE IF NOT EXISTS users(
                id SERIAL PRIMARY KEY, username TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'worker', password_hash TEXT NOT NULL,
                must_change_password BOOLEAN NOT NULL DEFAULT TRUE, active BOOLEAN NOT NULL DEFAULT TRUE)""")
            c.execute("""CREATE TABLE IF NOT EXISTS cars(
                id SERIAL PRIMARY KEY, plate TEXT NOT NULL, company TEXT, service TEXT,
                worker_id INTEGER REFERENCES users(id), started_at TIMESTAMPTZ DEFAULT NOW(),
                finished_at TIMESTAMPTZ, checklist_status TEXT DEFAULT 'pending')""")
            for n,d in [("brand","TEXT"),("model","TEXT"),("target_minutes","INTEGER"),("actual_minutes","NUMERIC"),("photo_path","TEXT"),("checklist_status","TEXT DEFAULT 'pending'")]: col(c,"cars",n,d)
            c.execute("""CREATE TABLE IF NOT EXISTS contasimple_config(
                id INTEGER PRIMARY KEY CHECK(id=1),
                access_token TEXT, refresh_token TEXT, expires_at TIMESTAMPTZ,
                connected_at TIMESTAMPTZ,
                site_url TEXT, api_url TEXT, client_id TEXT, client_secret TEXT,
                redirect_uri TEXT, albaran_endpoint TEXT
            )""")
            for n in ["site_url TEXT","api_url TEXT","client_id TEXT","client_secret TEXT","redirect_uri TEXT","albaran_endpoint TEXT"]:
                col(c,"contasimple_config",""+n.split()[0],n.split()[1])
            c.execute("""CREATE TABLE IF NOT EXISTS service_prices(
                id SERIAL PRIMARY KEY, company TEXT NOT NULL, service TEXT NOT NULL,
                base_price NUMERIC(12,2) NOT NULL DEFAULT 0,
                UNIQUE(company,service)
            )""")
            c.execute("""CREATE TABLE IF NOT EXISTS albaran_sync(
                id SERIAL PRIMARY KEY, car_id INTEGER UNIQUE REFERENCES cars(id) ON DELETE CASCADE,
                status TEXT NOT NULL DEFAULT 'pending', external_id TEXT,
                error TEXT, created_at TIMESTAMPTZ DEFAULT NOW()
            )""")
            c.execute("""CREATE TABLE IF NOT EXISTS checklist_items(
                id SERIAL PRIMARY KEY, car_id INTEGER REFERENCES cars(id) ON DELETE CASCADE,
                item TEXT NOT NULL, ok BOOLEAN DEFAULT FALSE, reviewed BOOLEAN DEFAULT FALSE,
                UNIQUE(car_id,item))""")
            c.execute("ALTER TABLE checklist_items ADD COLUMN IF NOT EXISTS status TEXT DEFAULT NULL")
            c.execute("""UPDATE checklist_items
                         SET status=CASE WHEN reviewed THEN 'repasado' WHEN ok THEN 'ok' ELSE NULL END
                         WHERE status IS NULL""")
            defaults=[("admin","Administrador","admin","admin123")]+[(x.lower(),x,"worker","1234") for x in WORKERS]
            for u,n,r,p in defaults:
                c.execute("""INSERT INTO users(username,name,role,password_hash,must_change_password)
                             VALUES(%s,%s,%s,%s,TRUE) ON CONFLICT(username) DO NOTHING""",(u,n,r,generate_password_hash(p)))
            c.execute("""UPDATE cars SET target_minutes=CASE service
                WHEN 'Lavado exterior' THEN 30 WHEN 'Lavado interior + exterior' THEN 90
                WHEN 'Limpieza integral' THEN 150 WHEN 'Repaso' THEN 15 ELSE target_minutes END
                WHERE target_minutes IS NULL""")
        conn.commit()

def me():
    uid=session.get("user_id")
    if not uid:return None
    with db() as conn:
        with conn.cursor() as c:
            c.execute("SELECT id,username,name,role,must_change_password FROM users WHERE id=%s AND active=TRUE",(uid,))
            return c.fetchone()

def auth(fn):
    @wraps(fn)
    def w(*a,**k):
        u=me()
        if not u:return redirect(url_for("login"))
        if u[4] and request.endpoint!="change_password":return redirect(url_for("change_password"))
        return fn(*a,**k)
    return w

def admin(fn):
    @wraps(fn)
    def w(*a,**k):
        u=me()
        if not u or u[3]!="admin":return redirect(url_for("home"))
        if u[4]:return redirect(url_for("change_password"))
        return fn(*a,**k)
    return w


def contasimple_settings():
    with db() as conn:
        with conn.cursor() as c:
            c.execute("""SELECT site_url,api_url,client_id,client_secret,redirect_uri,albaran_endpoint,
                                access_token,refresh_token,expires_at
                         FROM contasimple_config WHERE id=1""")
            row=c.fetchone()
    if not row:
        return {"site_url":CONTASIMPLE_SITE_URL,"api_url":CONTASIMPLE_API_URL,
                "client_id":CONTASIMPLE_CLIENT_ID,"client_secret":CONTASIMPLE_CLIENT_SECRET,
                "redirect_uri":CONTASIMPLE_REDIRECT_URI,"albaran_endpoint":CONTASIMPLE_ALBARAN_ENDPOINT,
                "access_token":None,"refresh_token":None,"expires_at":None}
    return {"site_url":row[0] or CONTASIMPLE_SITE_URL,"api_url":row[1] or CONTASIMPLE_API_URL,
            "client_id":row[2] or CONTASIMPLE_CLIENT_ID,"client_secret":row[3] or CONTASIMPLE_CLIENT_SECRET,
            "redirect_uri":row[4] or CONTASIMPLE_REDIRECT_URI,"albaran_endpoint":row[5] or CONTASIMPLE_ALBARAN_ENDPOINT,
            "access_token":row[6],"refresh_token":row[7],"expires_at":row[8]}

def contasimple_authorize_url():
    cfg=contasimple_settings()
    if not (cfg["site_url"] and cfg["client_id"] and cfg["redirect_uri"]): return None
    from urllib.parse import urlencode
    q=urlencode({"response_type":"code","client_id":cfg["client_id"],
                 "redirect_uri":cfg["redirect_uri"],"scope":"offline_access"})
    return cfg["site_url"].rstrip("/")+"/login.aspx?"+q

def contasimple_tokens():
    cfg=contasimple_settings()
    return (cfg["access_token"],cfg["refresh_token"],cfg["expires_at"]) if cfg else None

def contasimple_refresh():
    cfg=contasimple_settings()
    if not cfg["refresh_token"] or not cfg["api_url"]: return False
    try:
        r=requests.post(cfg["api_url"].rstrip("/")+"/oauth/token",data={
            "grant_type":"refresh_token","client_id":cfg["client_id"],
            "client_secret":cfg["client_secret"],"refresh_token":cfg["refresh_token"]},timeout=20)
        r.raise_for_status(); data=r.json()
        from datetime import datetime,timedelta,timezone
        exp=datetime.now(timezone.utc)+timedelta(seconds=int(data.get("expires_in",3600)))
        with db() as conn:
            with conn.cursor() as c:
                c.execute("UPDATE contasimple_config SET access_token=%s,refresh_token=%s,expires_at=%s WHERE id=1",
                          (data.get("access_token"),data.get("refresh_token",cfg["refresh_token"]),exp))
            conn.commit()
        return True
    except Exception:return False

def contasimple_create_albaran(car_id):
    cfg=contasimple_settings()
    if not cfg["albaran_endpoint"]: return False,"Falta configurar el endpoint de albaranes"
    with db() as conn:
        with conn.cursor() as c:
            c.execute("""SELECT c.id,c.plate,c.company,c.service,c.finished_at,u.name,
                                COALESCE(sp.base_price,0)
                         FROM cars c JOIN users u ON u.id=c.worker_id
                         LEFT JOIN service_prices sp ON sp.company=c.company AND sp.service=c.service
                         WHERE c.id=%s""",(car_id,))
            car=c.fetchone()
            c.execute("INSERT INTO albaran_sync(car_id,status) VALUES(%s,'pending') ON CONFLICT(car_id) DO NOTHING",(car_id,))
            conn.commit()
    if not car:return False,"Coche no encontrado"
    if not cfg["access_token"]:return False,"Contasimple no está conectado"
    payload={"plate":car[1],"company":car[2],"service":car[3],
             "date":car[4].isoformat() if car[4] else None,
             "base_price":float(car[6]),"worker":car[5]}
    try:
        r=requests.post(cfg["albaran_endpoint"],
                        headers={"Authorization":"Bearer "+cfg["access_token"],"Content-Type":"application/json"},
                        json=payload,timeout=20)
        if r.status_code==401 and contasimple_refresh():
            cfg=contasimple_settings()
            r=requests.post(cfg["albaran_endpoint"],
                            headers={"Authorization":"Bearer "+cfg["access_token"],"Content-Type":"application/json"},
                            json=payload,timeout=20)
        r.raise_for_status()
        data=r.json() if r.content else {}
        ext=str(data.get("id") or data.get("number") or data.get("albaran_id") or "")
        with db() as conn:
            with conn.cursor() as c:c.execute("UPDATE albaran_sync SET status='sent',external_id=%s,error=NULL WHERE car_id=%s",(ext,car_id))
            conn.commit()
        return True,ext
    except Exception as e:
        with db() as conn:
            with conn.cursor() as c:c.execute("UPDATE albaran_sync SET status='error',error=%s WHERE car_id=%s",(str(e)[:1000],car_id))
            conn.commit()
        return False,str(e)[:300]

@app.context_processor
def ctx(): return {"user":me(),"companies":COMPANIES,"services":SERVICES}
@app.route("/admin/contasimple/settings",methods=["POST"])
@admin
def contasimple_settings_save():
    fields={
        "site_url":request.form.get("site_url","").strip(),
        "api_url":request.form.get("api_url","").strip(),
        "client_id":request.form.get("client_id","").strip(),
        "client_secret":request.form.get("client_secret","").strip(),
        "redirect_uri":request.form.get("redirect_uri","").strip(),
        "albaran_endpoint":request.form.get("albaran_endpoint","").strip()
    }
    with db() as conn:
        with conn.cursor() as c:
            c.execute("""INSERT INTO contasimple_config(id,site_url,api_url,client_id,client_secret,redirect_uri,albaran_endpoint)
                         VALUES(1,%(site_url)s,%(api_url)s,%(client_id)s,%(client_secret)s,%(redirect_uri)s,%(albaran_endpoint)s)
                         ON CONFLICT(id) DO UPDATE SET site_url=EXCLUDED.site_url,api_url=EXCLUDED.api_url,
                         client_id=EXCLUDED.client_id,client_secret=EXCLUDED.client_secret,
                         redirect_uri=EXCLUDED.redirect_uri,albaran_endpoint=EXCLUDED.albaran_endpoint""",fields)
        conn.commit()
    flash("Credenciales y configuración de Contasimple guardadas.","success")
    return redirect(url_for("contasimple"))

@app.route("/admin/contasimple")
@admin
def contasimple():
    tok=contasimple_tokens()
    connected=bool(tok and tok[0])
    with db() as conn:
        with conn.cursor() as c:
            for company in COMPANIES:
                for service in SERVICES:
                    c.execute("INSERT INTO service_prices(company,service,base_price) VALUES(%s,%s,0) ON CONFLICT(company,service) DO NOTHING",(company,service))
            c.execute("SELECT company,service,base_price FROM service_prices ORDER BY id")
            prices=c.fetchall()
            c.execute("""SELECT a.car_id,a.status,a.external_id,a.error,c.plate,c.company,c.service
                         FROM albaran_sync a JOIN cars c ON c.id=a.car_id
                         ORDER BY a.created_at DESC LIMIT 100""")
            syncs=c.fetchall()
        conn.commit()
    return render_template("contasimple.html",connected=connected,auth_url=contasimple_authorize_url(),prices=prices,syncs=syncs,cfg=contasimple_settings())

@app.route("/admin/contasimple/prices",methods=["POST"])
@admin
def contasimple_prices():
    with db() as conn:
        with conn.cursor() as c:
            for company in COMPANIES:
                for service in SERVICES:
                    raw=request.form.get(f"p_{company}_{service}","0").replace(",",".")
                    try: price=max(0,float(raw or 0))
                    except: price=0
                    c.execute("""INSERT INTO service_prices(company,service,base_price) VALUES(%s,%s,%s)
                                 ON CONFLICT(company,service) DO UPDATE SET base_price=EXCLUDED.base_price""",
                              (company,service,price))
        conn.commit()
    flash("Tarifas guardadas.","success")
    return redirect(url_for("contasimple"))

@app.route("/admin/contasimple/callback")
@admin
def contasimple_callback():
    code=request.args.get("code")
    cfg=contasimple_settings()
    if not code or not cfg["api_url"]:return redirect(url_for("contasimple"))
    try:
        r=requests.post(cfg["api_url"].rstrip("/")+"/oauth/token",data={
            "grant_type":"authorization_code","client_id":cfg["client_id"],
            "client_secret":cfg["client_secret"],"code":code,
            "redirect_uri":cfg["redirect_uri"]},timeout=20)
        r.raise_for_status(); data=r.json()
        from datetime import datetime,timedelta,timezone
        exp=datetime.now(timezone.utc)+timedelta(seconds=int(data.get("expires_in",3600)))
        with db() as conn:
            with conn.cursor() as c:
                c.execute("""INSERT INTO contasimple_config(id,access_token,refresh_token,expires_at,connected_at)
                             VALUES(1,%s,%s,%s,NOW())
                             ON CONFLICT(id) DO UPDATE SET access_token=EXCLUDED.access_token,
                             refresh_token=EXCLUDED.refresh_token,expires_at=EXCLUDED.expires_at,connected_at=NOW()""",
                          (data.get("access_token"),data.get("refresh_token"),exp))
            conn.commit()
        flash("Contasimple conectado.","success")
    except Exception as e:flash("No se pudo conectar con Contasimple: "+str(e)[:200],"error")
    return redirect(url_for("contasimple"))

@app.route("/admin/contasimple/retry/<int:cid>",methods=["POST"])
@admin
def contasimple_retry(cid):
    ok,msg=contasimple_create_albaran(cid)
    flash(("Albarán enviado: " if ok else "No se pudo enviar: ")+str(msg),"success" if ok else "error")
    return redirect(url_for("contasimple"))



@app.route("/")
@auth
def home(): return redirect(url_for("admin_dashboard" if me()[3]=="admin" else "worker"))

@app.route("/login",methods=["GET","POST"])
def login():
    if request.method=="POST":
        u=request.form.get("username","").strip().lower(); p=request.form.get("password","")
        with db() as conn:
            with conn.cursor() as c:
                c.execute("SELECT id,password_hash FROM users WHERE username=%s AND active=TRUE",(u,)); r=c.fetchone()
        if r and check_password_hash(r[1],p): session["user_id"]=r[0]; return redirect(url_for("home"))
        flash("Usuario o contraseña incorrectos.","error")
    return render_template("login.html")

@app.route("/logout")
def logout(): session.clear(); return redirect(url_for("login"))

@app.route("/change-password",methods=["GET","POST"])
@auth
def change_password():
    u=me()
    if request.method=="POST":
        old=request.form.get("current_password",""); new=request.form.get("new_password",""); rep=request.form.get("confirm_password","")
        if len(new)<4: flash("La nueva contraseña debe tener al menos 4 caracteres.","error")
        elif new!=rep: flash("Las contraseñas no coinciden.","error")
        else:
            with db() as conn:
                with conn.cursor() as c:
                    c.execute("SELECT password_hash FROM users WHERE id=%s",(u[0],)); r=c.fetchone()
                    if not r or not check_password_hash(r[0],old): flash("La contraseña actual no es correcta.","error")
                    else:
                        c.execute("UPDATE users SET password_hash=%s,must_change_password=FALSE WHERE id=%s",(generate_password_hash(new),u[0])); conn.commit()
                        return redirect(url_for("home"))
    return render_template("change_password.html")

@app.route("/worker")
@auth
def worker():
    u=me()
    if u[3]=="admin":return redirect(url_for("admin_dashboard"))
    with db() as conn:
        with conn.cursor() as c:
            c.execute("""SELECT id,plate,brand,model,company,service,target_minutes,started_at
                         FROM cars WHERE worker_id=%s AND finished_at IS NULL ORDER BY started_at DESC LIMIT 1""",(u[0],)); active=c.fetchone()
            c.execute("""SELECT COUNT(*),COALESCE(AVG(actual_minutes),0) FROM cars
                         WHERE worker_id=%s AND finished_at>=NOW()-INTERVAL '7 days'""",(u[0],)); stats=c.fetchone()
            items=[]
            if active and active[5]=="Repaso":
                c.execute("SELECT id,item,ok,reviewed FROM checklist_items WHERE car_id=%s ORDER BY id",(active[0],)); items=c.fetchall()
    return render_template("worker.html",active=active,stats=stats,items=items)

@app.route("/start",methods=["POST"])
@auth
def start():
    u=me(); photo=request.files.get("photo")
    plate=request.form.get("plate","").strip().upper(); brand=request.form.get("brand","").strip(); model=request.form.get("model","").strip()
    company=request.form.get("company",""); service=request.form.get("service","")
    if u[3]=="admin":return redirect(url_for("admin_dashboard"))
    if not plate or company not in COMPANIES or service not in SERVICES or not photo or not photo.filename:
        flash("Foto, matrícula, empresa y servicio son obligatorios.","error"); return redirect(url_for("worker"))
    with db() as conn:
        with conn.cursor() as c:
            c.execute("SELECT 1 FROM cars WHERE worker_id=%s AND finished_at IS NULL",(u[0],))
            if c.fetchone(): flash("Ya tienes un coche en proceso.","error"); return redirect(url_for("worker"))
            ext=os.path.splitext(secure_filename(photo.filename))[1].lower() or ".jpg"
            fn=uuid.uuid4().hex+ext; photo.save(os.path.join(UPLOAD_DIR,fn))
            c.execute("""INSERT INTO cars(plate,brand,model,company,service,target_minutes,worker_id,photo_path,checklist_status)
                         VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                      (plate,brand,model,company,service,SERVICES[service],u[0],fn,"pending")); cid=c.fetchone()[0]
            if service=="Repaso":
                for item in CHECKLIST:c.execute("INSERT INTO checklist_items(car_id,item) VALUES(%s,%s)",(cid,item))
        conn.commit()
    return redirect(url_for("worker"))

@app.route("/photo/<path:name>")
@auth
def photo(name): return send_from_directory(UPLOAD_DIR,name)

@app.route("/checklist/<int:cid>",methods=["POST"])
@auth
def checklist_save(cid):
    u=me()
    with db() as conn:
        with conn.cursor() as c:
            c.execute("SELECT id FROM cars WHERE id=%s AND worker_id=%s AND service='Repaso' AND finished_at IS NULL",(cid,u[0],))
            if not c.fetchone():return redirect(url_for("worker"))
            for i,item in enumerate(CHECKLIST):
                status=request.form.get(f"status_{i}") or None
                c.execute("""UPDATE checklist_items
                             SET status=%s, ok=%s, reviewed=%s
                             WHERE car_id=%s AND item=%s""",
                          (status, status=="ok", status=="repasado", cid, item))
        conn.commit()
    flash("Checklist guardado.","success"); return redirect(url_for("worker"))

@app.route("/finish/<int:cid>",methods=["POST"])
@auth
def finish(cid):
    u=me()
    with db() as conn:
        with conn.cursor() as c:
            c.execute("SELECT service FROM cars WHERE id=%s AND worker_id=%s AND finished_at IS NULL",(cid,u[0],)); r=c.fetchone()
            if not r:return redirect(url_for("worker"))
            if r[0]=="Repaso":
                c.execute("SELECT COUNT(*) FROM checklist_items WHERE car_id=%s AND status IS NULL",(cid,))
                if c.fetchone()[0]:
                    flash("Completa todos los puntos del checklist.","error"); return redirect(url_for("worker"))
                c.execute("UPDATE cars SET checklist_status='done' WHERE id=%s",(cid,))
            c.execute("UPDATE cars SET finished_at=NOW(),actual_minutes=EXTRACT(EPOCH FROM(NOW()-started_at))/60 WHERE id=%s",(cid,))
        conn.commit()
    contasimple_create_albaran(cid)
    return redirect(url_for("worker"))

@app.route("/admin")
@admin
def admin_dashboard():
    with db() as conn:
        with conn.cursor() as c:
            c.execute("""SELECT COUNT(*),COALESCE(AVG(actual_minutes),0),
                COALESCE(AVG(CASE WHEN actual_minutes<=target_minutes THEN 100.0 ELSE target_minutes*100.0/NULLIF(actual_minutes,0) END),0)
                FROM cars WHERE finished_at>=NOW()-INTERVAL '7 days'"""); summary=c.fetchone()
            c.execute("SELECT COUNT(*) FROM cars WHERE finished_at IS NULL"); active=c.fetchone()[0]
            c.execute("SELECT COUNT(*) FROM cars WHERE service='Repaso' AND checklist_status='pending'"); pending=c.fetchone()[0]
            c.execute("""SELECT u.name,COUNT(c.id),COUNT(DISTINCT c.finished_at::date),COALESCE(AVG(c.actual_minutes),0)
                         FROM users u LEFT JOIN cars c ON c.worker_id=u.id AND c.finished_at>=NOW()-INTERVAL '7 days'
                         WHERE u.role='worker' AND u.active=TRUE GROUP BY u.id,u.name ORDER BY u.id"""); workers=c.fetchall()
    return render_template("admin.html",summary=summary,active=active,pending=pending,workers=workers)

@app.route("/admin/cars")
@admin
def admin_cars():
    with db() as conn:
        with conn.cursor() as c:
            c.execute("""SELECT c.id,c.plate,c.brand,c.model,c.company,c.service,u.name,c.actual_minutes,c.target_minutes,c.checklist_status,c.photo_path,
                         CASE WHEN c.service <> 'Repaso' AND NOT EXISTS (
                              SELECT 1 FROM cars cl
                              WHERE cl.plate=c.plate AND cl.service='Repaso'
                                AND cl.finished_at IS NOT NULL
                                AND cl.checklist_status='done'
                                AND cl.started_at >= c.started_at
                         ) THEN TRUE ELSE FALSE END AS checklist_pending
                         FROM cars c JOIN users u ON u.id=c.worker_id
                         WHERE c.service <> 'Repaso'
                         ORDER BY c.started_at DESC LIMIT 500"""); cars=c.fetchall()
    return render_template("cars.html",cars=cars)

@app.route("/admin/checklists")
@admin
def admin_checklists():
    with db() as conn:
        with conn.cursor() as c:
            c.execute("""SELECT c.id,c.plate,c.brand,c.model,c.company,u.name,c.started_at
                         FROM cars c JOIN users u ON u.id=c.worker_id
                         WHERE c.service <> 'Repaso'
                           AND NOT EXISTS (
                               SELECT 1 FROM cars cl
                               WHERE cl.plate=c.plate AND cl.service='Repaso'
                                 AND cl.finished_at IS NOT NULL
                                 AND cl.checklist_status='done'
                                 AND cl.started_at >= c.started_at
                           )
                           AND c.id = (
                               SELECT c2.id FROM cars c2
                               WHERE c2.plate=c.plate AND c2.service <> 'Repaso'
                               ORDER BY c2.started_at DESC LIMIT 1
                           )
                         ORDER BY c.started_at DESC"""); rows=c.fetchall()
    return render_template("checklists.html",rows=rows)

@app.route("/admin/workers")
@admin
def admin_workers():
    with db() as conn:
        with conn.cursor() as c:
            c.execute("SELECT id,username,name,active,must_change_password FROM users WHERE role='worker' ORDER BY id"); rows=c.fetchall()
    return render_template("workers.html",rows=rows)

@app.route("/admin/reset/<int:uid>",methods=["POST"])
@admin
def reset(uid):
    with db() as conn:
        with conn.cursor() as c:c.execute("UPDATE users SET password_hash=%s,must_change_password=TRUE WHERE id=%s AND role='worker'",(generate_password_hash("1234"),uid))
        conn.commit()
    flash("Contraseña restablecida a 1234.","success"); return redirect(url_for("admin_workers"))

@app.route("/health")
def health():return "OK",200

with app.app_context():init_db()
if __name__=="__main__":app.run(host="0.0.0.0",port=int(os.environ.get("PORT","8080")))
