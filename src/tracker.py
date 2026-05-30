"""
Libro de Vuelo — ACARS
Requiere: pip install SimConnect requests
FSUIPC7 instalado (gratis)
"""

import tkinter as tk
from tkinter import messagebox
import threading
import time
import json
import requests
import os
from datetime import datetime, timezone

# ── CONFIG ────────────────────────────────────────────────────────────────────

SUPABASE_URL = "https://xsvnxexbkrpnwfxhfoho.supabase.co"
SUPABASE_KEY = "sb_publishable_5VQjgvOKq1k_wgpLegG1hA_AGj15Bq_"
CONFIG_FILE  = os.path.join(os.path.expanduser("~"), ".librovuelo_acars.json")
VERSION      = "1.0"

# ── PERSISTENCIA ──────────────────────────────────────────────────────────────

def load_config():
    try:
        with open(CONFIG_FILE) as f:
            return json.load(f)
    except:
        return {}

def save_config(data):
    with open(CONFIG_FILE, "w") as f:
        json.dump(data, f)

# ── SUPABASE ──────────────────────────────────────────────────────────────────

def sb_login(email, password):
    try:
        r = requests.post(
            f"{SUPABASE_URL}/auth/v1/token?grant_type=password",
            headers={"apikey": SUPABASE_KEY, "Content-Type": "application/json"},
            json={"email": email, "password": password}, timeout=10
        )
        if r.status_code == 200:
            return r.json()
    except:
        pass
    return None

def sb_refresh(refresh_token):
    try:
        r = requests.post(
            f"{SUPABASE_URL}/auth/v1/token?grant_type=refresh_token",
            headers={"apikey": SUPABASE_KEY, "Content-Type": "application/json"},
            json={"refresh_token": refresh_token}, timeout=10
        )
        if r.status_code == 200:
            return r.json()
    except:
        pass
    return None

def sb_insert_flight(token, user_id, flight):
    try:
        r = requests.post(
            f"{SUPABASE_URL}/rest/v1/flights",
            headers={
                "apikey": SUPABASE_KEY,
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "Prefer": "return=representation"
            },
            json={**flight, "pilot_id": user_id}, timeout=10
        )
        return r.status_code in (200, 201)
    except:
        return False

# ── SIMBRIEF ──────────────────────────────────────────────────────────────────

def fetch_simbrief(username):
    """Trae el último OFP del piloto desde SimBrief"""
    try:
        r = requests.get(
            f"https://www.simbrief.com/api/xml.fetcher.php?username={username}&json=1",
            timeout=10
        )
        if r.status_code == 200:
            d = r.json()
            origin  = d.get("origin", {}).get("icao_code", "")
            dest    = d.get("destination", {}).get("icao_code", "")
            airline = d.get("airline", "")
            aircraft= d.get("aircraft", {}).get("icaocode", "")
            blk_min = int(d.get("times", {}).get("sched_block", 0)) // 60
            return {
                "origin":   origin.upper(),
                "dest":     dest.upper(),
                "airline":  airline.upper(),
                "aircraft": aircraft.upper(),
                "est_min":  blk_min,
            }
    except:
        pass
    return None

# ── SIMCONNECT ────────────────────────────────────────────────────────────────

def try_simconnect():
    """Intenta conectar a SimConnect, devuelve (sm, aq) o (None, None)"""
    try:
        from SimConnect import SimConnect, AircraftRequests
        sm = SimConnect()
        aq = AircraftRequests(sm, _time=100)
        return sm, aq
    except:
        return None, None

def read_sim(aq):
    """Lee variables del sim, devuelve dict con datos o None"""
    try:
        alt      = aq.get("PLANE_ALTITUDE")
        on_gnd   = aq.get("SIM_ON_GROUND")
        vs       = aq.get("VERTICAL_SPEED")
        lat      = aq.get("PLANE_LATITUDE")
        lon      = aq.get("PLANE_LONGITUDE")
        aircraft = aq.get("TITLE")

        return {
            "altitude_ft": float(alt) if alt is not None else 0,
            "on_ground":   bool(int(on_gnd)) if on_gnd is not None else True,
            "vs_fpm":      float(vs) * 60 if vs is not None else 0,  # ft/s → ft/min
            "lat":         float(lat) if lat is not None else 0,
            "lon":         float(lon) if lon is not None else 0,
            "aircraft":    str(aircraft).strip() if aircraft else "",
        }
    except:
        return None

def get_nearest_icao(lat, lon):
    """Busca el ICAO del aeropuerto más cercano vía Overpass"""
    try:
        delta = 0.15
        query = f"""
[out:json][timeout:8];
node["aeroway"="aerodrome"]["icao"](
  {lat-delta},{lon-delta},{lat+delta},{lon+delta}
);
out 1;
"""
        r = requests.post(
            "https://overpass-api.de/api/interpreter",
            data={"data": query}, timeout=10
        )
        if r.status_code == 200:
            els = r.json().get("elements", [])
            if els:
                icao = els[0].get("tags", {}).get("icao", "")
                if icao:
                    return icao[:4].upper()
    except:
        pass
    return "????"

# ── VALIDACIÓN ────────────────────────────────────────────────────────────────

def validate(plan, actual):
    """
    Compara plan SimBrief vs datos reales.
    Devuelve (ok, mensaje)
    """
    errors = []

    # Origen
    if plan["origin"] and actual["origin"] != plan["origin"]:
        errors.append(f"Origen: voló desde {actual['origin']}, plan era {plan['origin']}")

    # Destino
    if plan["dest"] and actual["dest"] != plan["dest"]:
        errors.append(f"Destino: aterrizó en {actual['dest']}, plan era {plan['dest']}")

    # Duración ±20%
    if plan["est_min"] > 0:
        min_ok = plan["est_min"] * 0.80
        max_ok = plan["est_min"] * 1.20
        if not (min_ok <= actual["duration"] <= max_ok):
            errors.append(
                f"Duración: {actual['duration']} min (plan: {plan['est_min']} min ±20%)"
            )

    if errors:
        return False, "\n".join(errors)
    return True, "OK"

# ── APP ───────────────────────────────────────────────────────────────────────

class ACARS:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title(f"Libro de Vuelo — ACARS v{VERSION}")
        self.root.geometry("460x620")
        self.root.resizable(True, True)
        self.root.configure(bg="#0d0f14")

        self.cfg     = load_config()
        self.token   = self.cfg.get("access_token")
        self.rtoken  = self.cfg.get("refresh_token")
        self.uid     = self.cfg.get("user_id")

        # Estado vuelo
        self.running       = False
        self.in_flight     = False
        self.flight_start  = None
        self.departure     = ""
        self.aircraft      = ""
        self.prev_on_ground= True
        self.vs_buf        = []
        self.last_td_fpm   = 0
        self.last_lat      = 0
        self.last_lon      = 0

        # SimBrief plan
        self.plan = None

        # SimConnect
        self.sm = None
        self.aq = None

        self.build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        if self.token and self.uid:
            self.show_main()
        else:
            self.show_login()

    # ── UI LOGIN ──────────────────────────────────────────────────────────────

    def build_ui(self):
        self.f_login = tk.Frame(self.root, bg="#0d0f14")
        self.f_main  = tk.Frame(self.root, bg="#0d0f14")

    def clear(self, f):
        for w in f.winfo_children():
            w.destroy()

    def show_login(self):
        self.f_main.pack_forget()
        self.f_login.pack(fill="both", expand=True, padx=30)
        self.clear(self.f_login)

        tk.Label(self.f_login, text="✈  Libro de Vuelo",
                 font=("Georgia", 22, "bold"), fg="#e8d5a3", bg="#0d0f14").pack(pady=(40,4))
        tk.Label(self.f_login, text="ACARS — ingresá tu cuenta",
                 font=("Courier", 10), fg="#555", bg="#0d0f14").pack(pady=(0,30))

        # Email
        tk.Label(self.f_login, text="EMAIL", font=("Courier", 9),
                 fg="#888", bg="#0d0f14", anchor="w").pack(fill="x")
        self.v_email = tk.StringVar(value=self.cfg.get("email",""))
        tk.Entry(self.f_login, textvariable=self.v_email, font=("Courier", 13),
                 bg="#1a1d24", fg="#e8d5a3", insertbackground="#e8d5a3",
                 relief="flat", bd=8).pack(fill="x", pady=(2,12), ipady=6)

        # Password
        tk.Label(self.f_login, text="CONTRASEÑA", font=("Courier", 9),
                 fg="#888", bg="#0d0f14", anchor="w").pack(fill="x")
        self.v_pass = tk.StringVar()
        tk.Entry(self.f_login, textvariable=self.v_pass, font=("Courier", 13),
                 bg="#1a1d24", fg="#e8d5a3", insertbackground="#e8d5a3",
                 show="•", relief="flat", bd=8).pack(fill="x", pady=(2,20), ipady=6)

        self.lbl_err = tk.Label(self.f_login, text="", font=("Courier", 9),
                                fg="#ff6666", bg="#0d0f14")
        self.lbl_err.pack()

        tk.Button(self.f_login, text="INGRESAR", font=("Courier", 12, "bold"),
                  bg="#e8d5a3", fg="#0d0f14", relief="flat", cursor="hand2",
                  command=self.do_login).pack(fill="x", pady=(12,0), ipady=12)

    def do_login(self):
        email = self.v_email.get().strip()
        pwd   = self.v_pass.get().strip()
        if not email or not pwd:
            self.lbl_err.config(text="Completá los dos campos")
            return
        self.lbl_err.config(text="Conectando...", fg="#888")
        self.root.update()
        res = sb_login(email, pwd)
        if res and "access_token" in res:
            self.token  = res["access_token"]
            self.rtoken = res.get("refresh_token")
            self.uid    = res["user"]["id"]
            cfg = {"email": email, "access_token": self.token,
                   "refresh_token": self.rtoken, "user_id": self.uid}
            save_config(cfg)
            self.cfg = cfg
            self.show_main()
        else:
            self.lbl_err.config(text="Email o contraseña incorrectos", fg="#ff6666")

    # ── UI MAIN ───────────────────────────────────────────────────────────────

    def show_main(self):
        self.f_login.pack_forget()
        self.f_main.pack(fill="both", expand=True, padx=14, pady=10)
        self.clear(self.f_main)

        # Header
        tk.Label(self.f_main, text="Libro de Vuelo — ACARS",
                 font=("Georgia", 15, "bold"), fg="#e8d5a3", bg="#0d0f14").pack(pady=(6,0))
        tk.Label(self.f_main, text=self.cfg.get("email",""),
                 font=("Courier", 9), fg="#444", bg="#0d0f14").pack()

        # ── SimBrief ──────────────────────────────────────────────────────────
        sb_frame = tk.Frame(self.f_main, bg="#1a1d24", pady=10, padx=14)
        sb_frame.pack(fill="x", pady=(10,4))

        tk.Label(sb_frame, text="SIMBRIEF", font=("Courier", 8),
                 fg="#666", bg="#1a1d24").pack(anchor="w")

        row = tk.Frame(sb_frame, bg="#1a1d24")
        row.pack(fill="x", pady=(4,0))

        self.v_sbuser = tk.StringVar(value=self.cfg.get("sbuser",""))
        tk.Entry(row, textvariable=self.v_sbuser, font=("Courier", 12),
                 bg="#0d0f14", fg="#e8d5a3", insertbackground="#e8d5a3",
                 relief="flat", bd=6, width=18,
                 ).pack(side="left", ipady=5)

        tk.Button(row, text="CARGAR PLAN", font=("Courier", 9, "bold"),
                  bg="#e8d5a3", fg="#0d0f14", relief="flat", cursor="hand2",
                  command=self.load_plan).pack(side="left", padx=(8,0), ipady=5, ipadx=8)

        self.lbl_plan = tk.Label(sb_frame, text="Sin plan cargado",
                                  font=("Courier", 9), fg="#555", bg="#1a1d24")
        self.lbl_plan.pack(anchor="w", pady=(6,0))

        # ── Estado sim ────────────────────────────────────────────────────────
        sim_frame = tk.Frame(self.f_main, bg="#1a1d24", pady=10, padx=14)
        sim_frame.pack(fill="x", pady=(0,4))

        tk.Label(sim_frame, text="SIMULADOR", font=("Courier", 8),
                 fg="#666", bg="#1a1d24").pack(anchor="w")
        self.lbl_sim = tk.Label(sim_frame, text="⬤  Sin conexión",
                                 font=("Courier", 11), fg="#ff4444", bg="#1a1d24")
        self.lbl_sim.pack(anchor="w", pady=(4,0))

        # ── Vuelo en curso ────────────────────────────────────────────────────
        fl_frame = tk.Frame(self.f_main, bg="#1a1d24", pady=12, padx=14)
        fl_frame.pack(fill="x", pady=(0,4))

        tk.Label(fl_frame, text="VUELO EN CURSO", font=("Courier", 8),
                 fg="#666", bg="#1a1d24").pack(anchor="w")

        self.lbl_aircraft = tk.Label(fl_frame, text="",
                                      font=("Courier", 9), fg="#666", bg="#1a1d24")
        self.lbl_aircraft.pack(anchor="w", pady=(2,6))

        ruta = tk.Frame(fl_frame, bg="#1a1d24")
        ruta.pack(fill="x")
        self.lbl_dep  = tk.Label(ruta, text="----", font=("Georgia", 28, "bold"),
                                   fg="#e8d5a3", bg="#1a1d24")
        self.lbl_dep.pack(side="left")
        tk.Label(ruta, text=" → ", font=("Georgia", 22), fg="#444", bg="#1a1d24").pack(side="left")
        self.lbl_dest = tk.Label(ruta, text="----", font=("Georgia", 28, "bold"),
                                   fg="#e8d5a3", bg="#1a1d24")
        self.lbl_dest.pack(side="left")

        datos = tk.Frame(fl_frame, bg="#1a1d24")
        datos.pack(fill="x", pady=(10,0))
        for txt, attr, val in [("DURACIÓN","lbl_time","00:00:00"),
                                 ("ALTITUD", "lbl_alt", "-- ft"),
                                 ("V/S",     "lbl_vs",  "-- fpm")]:
            col = tk.Frame(datos, bg="#1a1d24")
            col.pack(side="left", expand=True)
            tk.Label(col, text=txt, font=("Courier", 8), fg="#555", bg="#1a1d24").pack()
            lbl = tk.Label(col, text=val, font=("Courier", 12, "bold"),
                           fg="#e8d5a3", bg="#1a1d24")
            lbl.pack()
            setattr(self, attr, lbl)

        # ── Último touchdown ──────────────────────────────────────────────────
        td_frame = tk.Frame(self.f_main, bg="#1a1d24", pady=10, padx=14)
        td_frame.pack(fill="x", pady=(0,4))
        tk.Label(td_frame, text="ÚLTIMO TOUCHDOWN", font=("Courier", 8),
                 fg="#666", bg="#1a1d24").pack(anchor="w")
        self.lbl_td = tk.Label(td_frame, text="—",
                                font=("Georgia", 18, "bold"), fg="#e8d5a3", bg="#1a1d24")
        self.lbl_td.pack(anchor="w", pady=(4,0))
        self.lbl_td_rate = tk.Label(td_frame, text="",
                                     font=("Courier", 9), fg="#00ffaa", bg="#1a1d24")
        self.lbl_td_rate.pack(anchor="w")

        # ── Log ───────────────────────────────────────────────────────────────
        log_frame = tk.Frame(self.f_main, bg="#1a1d24", pady=8, padx=14)
        log_frame.pack(fill="both", expand=True, pady=(0,8))
        tk.Label(log_frame, text="ACTIVIDAD", font=("Courier", 8),
                 fg="#666", bg="#1a1d24").pack(anchor="w")
        self.log_box = tk.Text(log_frame, font=("Courier", 8), bg="#0d0f14",
                                fg="#666", relief="flat", state="disabled", height=5)
        self.log_box.pack(fill="both", expand=True, pady=(4,0))

        # ── Botones ───────────────────────────────────────────────────────────
        btns = tk.Frame(self.f_main, bg="#0d0f14")
        btns.pack(fill="x")
        self.btn_main = tk.Button(btns, text="CONECTAR AL SIM",
                                   font=("Courier", 11, "bold"),
                                   bg="#e8d5a3", fg="#0d0f14", relief="flat",
                                   cursor="hand2", command=self.toggle)
        self.btn_main.pack(side="left", fill="x", expand=True, ipady=10, padx=(0,6))
        tk.Button(btns, text="Salir", font=("Courier", 9),
                  bg="#1a1d24", fg="#666", relief="flat", cursor="hand2",
                  command=self.logout).pack(side="right", ipady=10, ipadx=10)

    # ── SIMBRIEF ──────────────────────────────────────────────────────────────

    def load_plan(self):
        user = self.v_sbuser.get().strip()
        if not user:
            self.lbl_plan.config(text="⚠ Ingresá tu usuario de SimBrief", fg="#ff8800")
            return
        self.lbl_plan.config(text="Cargando...", fg="#888")
        self.root.update()

        # Guardar usuario SimBrief
        cfg = self.cfg.copy()
        cfg["sbuser"] = user
        save_config(cfg)
        self.cfg = cfg

        def _fetch():
            plan = fetch_simbrief(user)
            if plan:
                self.plan = plan
                txt = f"✓  {plan['origin']} → {plan['dest']}  |  {plan['aircraft']}  |  ~{plan['est_min']} min"
                self.root.after(0, lambda: self.lbl_plan.config(text=txt, fg="#00ffaa"))
                self.root.after(0, lambda: self.log(f"Plan SimBrief: {plan['origin']} → {plan['dest']}"))
            else:
                self.root.after(0, lambda: self.lbl_plan.config(
                    text="⚠ No se encontró plan. ¿Generaste el OFP?", fg="#ff8800"))

        threading.Thread(target=_fetch, daemon=True).start()

    # ── LOG ───────────────────────────────────────────────────────────────────

    def log(self, msg):
        ts = datetime.now().strftime("%H:%M:%S")
        self.log_box.config(state="normal")
        self.log_box.insert("end", f"[{ts}] {msg}\n")
        self.log_box.see("end")
        self.log_box.config(state="disabled")

    # ── CONEXIÓN ──────────────────────────────────────────────────────────────

    def toggle(self):
        if self.running:
            self.running = False
            self.btn_main.config(text="CONECTAR AL SIM")
            self.lbl_sim.config(text="⬤  Desconectado", fg="#ff4444")
            if self.sm:
                try: self.sm.exit()
                except: pass
            self.log("Tracker detenido.")
        else:
            self.running = True
            self.btn_main.config(text="DETENER")
            self.log("Conectando al simulador...")
            threading.Thread(target=self.loop, daemon=True).start()
            threading.Thread(target=self.timer_loop, daemon=True).start()

    def loop(self):
        # Conectar SimConnect
        self.sm, self.aq = try_simconnect()
        if not self.sm:
            self.root.after(0, lambda: self.log("⚠ MSFS no encontrado. ¿Está abierto?"))
            self.root.after(0, lambda: self.lbl_sim.config(
                text="⬤  MSFS no encontrado", fg="#ff8800"))
            # Reintentar cada 5 segundos
            while self.running:
                time.sleep(5)
                self.sm, self.aq = try_simconnect()
                if self.sm:
                    break
            if not self.running:
                return

        self.root.after(0, lambda: self.lbl_sim.config(
            text="⬤  Conectado — MSFS", fg="#00ffaa"))
        self.root.after(0, lambda: self.log("✓ Conectado al simulador"))

        while self.running:
            try:
                data = read_sim(self.aq)
                if data:
                    self.process(data)
                time.sleep(1)
            except Exception as e:
                self.root.after(0, lambda: self.lbl_sim.config(
                    text="⬤  Reconectando...", fg="#ff8800"))
                time.sleep(3)
                self.sm, self.aq = try_simconnect()
                if self.sm:
                    self.root.after(0, lambda: self.lbl_sim.config(
                        text="⬤  Conectado — MSFS", fg="#00ffaa"))

    def process(self, d):
        on_ground  = d["on_ground"]
        alt_ft     = d["altitude_ft"]
        vs_fpm     = d["vs_fpm"]
        aircraft   = d["aircraft"]
        lat        = d["lat"]
        lon        = d["lon"]

        # Guardar posición
        if lat: self.last_lat = lat
        if lon: self.last_lon = lon

        # Suavizar VS
        self.vs_buf.append(vs_fpm)
        if len(self.vs_buf) > 4: self.vs_buf.pop(0)
        vs_smooth = int(sum(self.vs_buf) / len(self.vs_buf))

        # Actualizar aeronave
        if aircraft and aircraft != self.aircraft:
            self.aircraft = aircraft
            short = aircraft[:40]
            self.root.after(0, lambda a=short: self.lbl_aircraft.config(text=a))

        # Actualizar displays
        self.root.after(0, lambda a=f"{int(alt_ft):,} ft": self.lbl_alt.config(text=a))
        self.root.after(0, lambda v=f"{vs_smooth:+,.0f} fpm": self.lbl_vs.config(text=v))

        # ── DESPEGUE ──────────────────────────────────────────────────────────
        if self.prev_on_ground and not on_ground and alt_ft > 30:
            self.in_flight    = True
            self.flight_start = time.time()
            self.vs_buf       = []

            # ICAO de salida por GPS
            dep = get_nearest_icao(self.last_lat, self.last_lon)
            self.departure = dep
            self.root.after(0, lambda d=dep: self.lbl_dep.config(text=d))
            self.root.after(0, lambda: self.lbl_dest.config(text="----"))
            self.root.after(0, lambda: self.log(f"✈ Despegue — {dep} / {self.aircraft[:25]}"))

        # ── ATERRIZAJE ────────────────────────────────────────────────────────
        if not self.prev_on_ground and on_ground and self.in_flight:
            self.in_flight  = False
            self.last_td_fpm = vs_smooth
            dur_min = int((time.time() - self.flight_start) / 60) if self.flight_start else 0

            # ICAO de llegada por GPS
            dest = get_nearest_icao(self.last_lat, self.last_lon)

            self.root.after(0, lambda d=dest: self.lbl_dest.config(text=d))
            self.root.after(0, lambda t=vs_smooth: self._show_td(t))
            self.root.after(0, lambda d=dest, t=vs_smooth:
                self.log(f"🛬 Aterrizaje — {d} — {t:+.0f} fpm"))

            threading.Thread(
                target=self._finish_flight,
                args=(dur_min, vs_smooth, dest), daemon=True
            ).start()

        self.prev_on_ground = on_ground

    def _show_td(self, fpm):
        a = abs(fpm)
        if a <= 180:   label, color = "PERFECTO 🏆", "#00ffaa"
        elif a <= 300: label, color = "BUENO ✅",    "#7fff00"
        elif a <= 500: label, color = "DURO ⚠️",     "#ffd700"
        elif a <= 700: label, color = "HARD LANDING 😬", "#ff8c00"
        else:          label, color = "CRASH 💥",    "#ff2244"
        self.lbl_td.config(text=f"{fpm:+.0f} fpm", fg=color)
        self.lbl_td_rate.config(text=label, fg=color)

    def _finish_flight(self, dur_min, td_fpm, dest):
        """Valida contra SimBrief y guarda si pasa"""
        actual = {
            "origin":   self.departure,
            "dest":     dest,
            "duration": dur_min,
            "aircraft": self.aircraft,
        }

        # Validar si hay plan SimBrief
        if self.plan:
            ok, msg = validate(self.plan, actual)
            if not ok:
                self.root.after(0, lambda m=msg: self.log(f"⚠ PIREP rechazado:\n{m}"))
                self.root.after(0, lambda: messagebox.showwarning(
                    "PIREP Rechazado",
                    f"El vuelo no coincide con el plan SimBrief:\n\n{msg}\n\nNo se guardó."
                ))
                return
            self.root.after(0, lambda: self.log("✓ Validación OK — guardando..."))
        else:
            self.root.after(0, lambda: self.log("Sin plan SimBrief — guardando sin validar..."))

        flight = {
            "date":        datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "airline":     self.plan["airline"] if self.plan else "",
            "aircraft":    self.aircraft,
            "departure":   self.departure,
            "destination": dest,
            "duration":    dur_min,
            "touchdown":   str(int(td_fpm)),
            "notes":       "ACARS automático",
        }

        ok = sb_insert_flight(self.token, self.uid, flight)
        if not ok and self.rtoken:
            res = sb_refresh(self.rtoken)
            if res:
                self.token  = res["access_token"]
                self.rtoken = res.get("refresh_token", self.rtoken)
                cfg = self.cfg.copy()
                cfg.update({"access_token": self.token, "refresh_token": self.rtoken})
                save_config(cfg)
                ok = sb_insert_flight(self.token, self.uid, flight)

        msg = "✅ Vuelo guardado en Libro de Vuelo!" if ok else "❌ Error al guardar"
        self.root.after(0, lambda: self.log(msg))

    def timer_loop(self):
        while self.running:
            if self.in_flight and self.flight_start:
                e = int(time.time() - self.flight_start)
                t = f"{e//3600:02d}:{(e%3600)//60:02d}:{e%60:02d}"
                self.root.after(0, lambda t=t: self.lbl_time.config(text=t))
            time.sleep(1)

    def logout(self):
        self.running = False
        if self.sm:
            try: self.sm.exit()
            except: pass
        save_config({})
        self.cfg = {}
        self.token = self.rtoken = self.uid = None
        self.show_login()

    def on_close(self):
        self.running = False
        if self.sm:
            try: self.sm.exit()
            except: pass
        self.root.destroy()

    def run(self):
        self.root.mainloop()

if __name__ == "__main__":
    app = ACARS()
    app.run()
