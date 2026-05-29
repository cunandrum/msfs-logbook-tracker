import tkinter as tk
import threading
import time
import json
import requests
import math
import re
import os
import glob
from datetime import datetime, timezone

SUPABASE_URL = "https://xsvnxexbkrpnwfxhfoho.supabase.co"
SUPABASE_KEY = "sb_publishable_5VQjgvOKq1k_wgpLegG1hA_AGj15Bq_"
CONFIG_FILE = os.path.join(os.path.expanduser("~"), ".msfs_logbook_config.json")

def load_config():
    try:
        with open(CONFIG_FILE) as f:
            return json.load(f)
    except:
        return {}

def save_config(data):
    with open(CONFIG_FILE, "w") as f:
        json.dump(data, f)

def supabase_login(email, password):
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

def supabase_insert_flight(access_token, user_id, flight):
    try:
        r = requests.post(
            f"{SUPABASE_URL}/rest/v1/flights",
            headers={
                "apikey": SUPABASE_KEY,
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
                "Prefer": "return=representation"
            },
            json={**flight, "pilot_id": user_id}, timeout=10
        )
        return r.status_code in (200, 201)
    except:
        return False

def find_msfs_report():
    """Find the MSFS AsoboReport file across common locations"""
    patterns = [
        os.path.expandvars(r"%LOCALAPPDATA%\Packages\Microsoft.FlightSimulator_8wekyb3d8bbwe\LocalState\AsoboReport-RunningSession.txt"),
        os.path.expandvars(r"%APPDATA%\Microsoft Flight Simulator\AsoboReport-RunningSession.txt"),
        os.path.expandvars(r"%LOCALAPPDATA%\Microsoft Flight Simulator\AsoboReport-RunningSession.txt"),
    ]
    # Also search in common Steam location
    steam_path = r"C:\Program Files (x86)\Steam\userdata"
    if os.path.exists(steam_path):
        for root, dirs, files in os.walk(steam_path):
            for f in files:
                if f == "AsoboReport-RunningSession.txt":
                    patterns.append(os.path.join(root, f))

    for p in patterns:
        if os.path.exists(p):
            return p
    return None

def parse_report(filepath):
    """Parse the MSFS report file and extract key values"""
    try:
        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()

        data = {}

        # Extract key values using regex
        def get_val(key, content):
            m = re.search(rf'^{key}=(.+)$', content, re.MULTILINE)
            return m.group(1).strip().strip('"') if m else None

        data["aircraft"] = get_val("UserContainerTitle", content)
        data["game_mode"] = get_val("GameMode", content)
        data["is_on_ground"] = get_val("IsOnGround", content)

        lat = get_val("Latitude", content)
        lon = get_val("Longitude", content)
        alt = get_val("Altitude", content)

        data["lat"] = float(lat) if lat else None
        data["lon"] = float(lon) if lon else None
        data["altitude"] = float(alt) if alt else None

        # Departure airport
        dep_match = re.search(r'Departure=\["([^"]+)",([^,]+),([^\]]+)\]', content)
        if dep_match:
            data["departure_name"] = dep_match.group(1)
            data["departure_lat"] = float(dep_match.group(2))
            data["departure_lon"] = float(dep_match.group(3))

        # Arrival airport
        arr_match = re.search(r'Arrival=\["([^"]+)",([^,]+),([^\]]+)\]', content)
        if arr_match:
            data["arrival_name"] = arr_match.group(1)

        return data
    except Exception as e:
        return None

def coords_to_icao_guess(lat, lon, name):
    """Try to get ICAO from departure name - rough mapping"""
    # MSFS departure names sometimes contain ICAO
    if name and len(name) == 4 and name.isupper():
        return name
    return name[:10] if name else "----"

def haversine(lat1, lon1, lat2, lon2):
    """Calculate distance in meters between two GPS points"""
    R = 6371000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlambda/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))

class MSFSTracker:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Libro de Vuelo - Tracker")
        self.root.geometry("480x700")
        self.root.minsize(440, 600)
        self.root.resizable(True, True)
        self.root.configure(bg="#0d0f14")

        self.config = load_config()
        self.access_token = self.config.get("access_token")
        self.user_id = self.config.get("user_id")
        self.running = False
        self.report_path = None
        self.in_flight = False
        self.flight_start_time = None
        self.departure_airport = "----"
        self.current_aircraft = ""
        self.on_ground_prev = True
        self.prev_alt = 0
        self.vs_samples = []

        self.build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        if self.access_token and self.user_id:
            self.show_tracker()
        else:
            self.show_login()

    def build_ui(self):
        self.frame_login = tk.Frame(self.root, bg="#0d0f14")
        self.frame_tracker = tk.Frame(self.root, bg="#0d0f14")

    def clear_frame(self, frame):
        for w in frame.winfo_children():
            w.destroy()

    def show_login(self):
        self.frame_tracker.pack_forget()
        self.frame_login.pack(fill="both", expand=True, padx=30, pady=30)
        self.clear_frame(self.frame_login)

        tk.Label(self.frame_login, text="MSFS 2020", font=("Courier", 10), fg="#666666", bg="#0d0f14").pack(pady=(20,0))
        tk.Label(self.frame_login, text="Libro de Vuelo", font=("Georgia", 22, "bold"), fg="#e8d5a3", bg="#0d0f14").pack(pady=(0,8))
        tk.Label(self.frame_login, text="Tracker automatico — sin instalacion", font=("Courier", 10), fg="#888888", bg="#0d0f14").pack(pady=(0,30))

        tk.Label(self.frame_login, text="EMAIL", font=("Courier", 9), fg="#888888", bg="#0d0f14", anchor="w").pack(fill="x")
        self.email_var = tk.StringVar(value=self.config.get("email",""))
        tk.Entry(self.frame_login, textvariable=self.email_var, font=("Courier", 13),
                 bg="#1a1d24", fg="#e8d5a3", insertbackground="#e8d5a3", relief="flat", bd=8).pack(fill="x", pady=(2,12), ipady=6)

        tk.Label(self.frame_login, text="CONTRASEÑA", font=("Courier", 9), fg="#888888", bg="#0d0f14", anchor="w").pack(fill="x")
        self.pass_var = tk.StringVar()
        tk.Entry(self.frame_login, textvariable=self.pass_var, font=("Courier", 13),
                 bg="#1a1d24", fg="#e8d5a3", insertbackground="#e8d5a3", show="•", relief="flat", bd=8).pack(fill="x", pady=(2,20), ipady=6)

        self.login_status = tk.Label(self.frame_login, text="", font=("Courier", 10), fg="#ff6666", bg="#0d0f14")
        self.login_status.pack()

        tk.Button(self.frame_login, text="INICIAR SESION", font=("Courier", 12, "bold"),
                  bg="#e8d5a3", fg="#0d0f14", relief="flat", bd=0, cursor="hand2",
                  command=self.do_login).pack(fill="x", pady=(10,0), ipady=12)

    def do_login(self):
        email = self.email_var.get().strip()
        password = self.pass_var.get().strip()
        if not email or not password:
            self.login_status.config(text="Ingresa email y contraseña")
            return
        self.login_status.config(text="Conectando...", fg="#888888")
        self.root.update()
        result = supabase_login(email, password)
        if result and "access_token" in result:
            self.access_token = result["access_token"]
            self.user_id = result["user"]["id"]
            cfg = {"email": email, "access_token": self.access_token, "user_id": self.user_id}
            save_config(cfg)
            self.config = cfg
            self.show_tracker()
        else:
            self.login_status.config(text="Email o contraseña incorrectos", fg="#ff6666")

    def show_tracker(self):
        self.frame_login.pack_forget()
        self.frame_tracker.pack(fill="both", expand=True, padx=16, pady=16)
        self.clear_frame(self.frame_tracker)

        tk.Label(self.frame_tracker, text="Libro de Vuelo — Tracker", font=("Georgia", 16, "bold"),
                 fg="#e8d5a3", bg="#0d0f14").pack(pady=(10,0))
        tk.Label(self.frame_tracker, text=self.config.get("email",""), font=("Courier", 9),
                 fg="#666666", bg="#0d0f14").pack(pady=(0,4))

        # Status
        sf = tk.Frame(self.frame_tracker, bg="#1a1d24", pady=10, padx=14)
        sf.pack(fill="x", pady=(8,8))
        tk.Label(sf, text="SIMULADOR", font=("Courier", 8), fg="#888888", bg="#1a1d24").pack(anchor="w")
        self.sim_status_label = tk.Label(sf, text="⬤  Desconectado", font=("Courier", 11), fg="#ff4444", bg="#1a1d24")
        self.sim_status_label.pack(anchor="w", pady=(4,0))

        # Flight info
        ff = tk.Frame(self.frame_tracker, bg="#1a1d24", pady=14, padx=14)
        ff.pack(fill="x", pady=(0,8))
        tk.Label(ff, text="VUELO EN CURSO", font=("Courier", 8), fg="#888888", bg="#1a1d24").pack(anchor="w")
        self.aircraft_label = tk.Label(ff, text="", font=("Courier", 9), fg="#888888", bg="#1a1d24")
        self.aircraft_label.pack(anchor="w", pady=(2,6))

        rf = tk.Frame(ff, bg="#1a1d24")
        rf.pack(fill="x")
        self.dep_label = tk.Label(rf, text="----", font=("Georgia", 26, "bold"), fg="#e8d5a3", bg="#1a1d24")
        self.dep_label.pack(side="left")
        tk.Label(rf, text=" → ", font=("Georgia", 20), fg="#666666", bg="#1a1d24").pack(side="left")
        self.dest_label = tk.Label(rf, text="----", font=("Georgia", 26, "bold"), fg="#e8d5a3", bg="#1a1d24")
        self.dest_label.pack(side="left")

        sf2 = tk.Frame(ff, bg="#1a1d24")
        sf2.pack(fill="x", pady=(10,0))
        for label, attr in [("DURACION","time_label"),("ALTITUD","alt_label"),("V/S","vs_label")]:
            col = tk.Frame(sf2, bg="#1a1d24")
            col.pack(side="left", expand=True)
            tk.Label(col, text=label, font=("Courier", 8), fg="#888888", bg="#1a1d24").pack()
            lbl = tk.Label(col, text="--:--", font=("Courier", 12, "bold"), fg="#e8d5a3", bg="#1a1d24")
            lbl.pack()
            setattr(self, attr, lbl)

        # Touchdown
        tf = tk.Frame(self.frame_tracker, bg="#1a1d24", pady=10, padx=14)
        tf.pack(fill="x", pady=(0,8))
        tk.Label(tf, text="ULTIMO TOUCHDOWN", font=("Courier", 8), fg="#888888", bg="#1a1d24").pack(anchor="w")
        self.td_label = tk.Label(tf, text="—", font=("Georgia", 18, "bold"), fg="#e8d5a3", bg="#1a1d24")
        self.td_label.pack(anchor="w", pady=(4,0))
        self.td_rating_label = tk.Label(tf, text="", font=("Courier", 9), fg="#00ffaa", bg="#1a1d24")
        self.td_rating_label.pack(anchor="w")

        # Log
        lf = tk.Frame(self.frame_tracker, bg="#1a1d24", pady=8, padx=14)
        lf.pack(fill="both", expand=True, pady=(0,8))
        tk.Label(lf, text="ACTIVIDAD", font=("Courier", 8), fg="#888888", bg="#1a1d24").pack(anchor="w")
        self.log_text = tk.Text(lf, font=("Courier", 8), bg="#0d0f14", fg="#666666", relief="flat", state="disabled")
        self.log_text.pack(fill="both", expand=True, pady=(4,0))

        # Buttons
        bf = tk.Frame(self.frame_tracker, bg="#0d0f14")
        bf.pack(fill="x", pady=(0,4))
        self.connect_btn = tk.Button(bf, text="CONECTAR A MSFS", font=("Courier", 11, "bold"),
                                      bg="#e8d5a3", fg="#0d0f14", relief="flat", bd=0, cursor="hand2",
                                      command=self.toggle_connection)
        self.connect_btn.pack(side="left", fill="x", expand=True, ipady=10, padx=(0,6))
        tk.Button(bf, text="Cerrar sesion", font=("Courier", 9), bg="#1a1d24", fg="#666666",
                  relief="flat", bd=0, cursor="hand2", command=self.logout).pack(side="right", ipady=10)

    def log(self, msg):
        ts = datetime.now().strftime("%H:%M:%S")
        self.log_text.config(state="normal")
        self.log_text.insert("end", f"[{ts}] {msg}\n")
        self.log_text.see("end")
        self.log_text.config(state="disabled")

    def toggle_connection(self):
        if self.running:
            self.running = False
            self.connect_btn.config(text="CONECTAR A MSFS")
            self.sim_status_label.config(text="⬤  Desconectado", fg="#ff4444")
            self.log("Tracker detenido.")
        else:
            self.running = True
            self.connect_btn.config(text="DETENER TRACKER")
            self.log("Buscando MSFS 2020...")
            threading.Thread(target=self.tracker_loop, daemon=True).start()
            threading.Thread(target=self.update_timer, daemon=True).start()

    def tracker_loop(self):
        while self.running:
            # Find report file
            if not self.report_path:
                path = find_msfs_report()
                if path:
                    self.report_path = path
                    self.root.after(0, lambda: self.sim_status_label.config(
                        text="⬤  Conectado a MSFS", fg="#00ffaa"))
                    self.root.after(0, lambda: self.log(f"Archivo MSFS encontrado!"))
                else:
                    self.root.after(0, lambda: self.sim_status_label.config(
                        text="⬤  MSFS no encontrado...", fg="#ff8800"))
                    time.sleep(5)
                    continue

            try:
                data = parse_report(self.report_path)
                if data:
                    self.process_data(data)
                time.sleep(2)
            except Exception as e:
                self.root.after(0, lambda: self.log(f"Error: {str(e)[:50]}"))
                time.sleep(3)

    def process_data(self, data):
        game_mode = data.get("game_mode", "")
        if "INGAME" not in game_mode and "FLIGHT" not in game_mode:
            return

        altitude_m = data.get("altitude", 0) or 0
        altitude_ft = altitude_m * 3.28084
        on_ground = data.get("is_on_ground", "true").lower() == "true"
        aircraft = data.get("aircraft", "") or ""

        # Calculate VS from altitude change
        vs_fpm = 0
        if self.prev_alt > 0:
            alt_change = altitude_ft - self.prev_alt
            vs_fpm = int(alt_change * 30)  # per 2 seconds * 30 = per minute
            self.vs_samples.append(vs_fpm)
            if len(self.vs_samples) > 3:
                self.vs_samples.pop(0)
            vs_fpm = int(sum(self.vs_samples) / len(self.vs_samples))
        self.prev_alt = altitude_ft

        if aircraft and aircraft != self.current_aircraft:
            self.current_aircraft = aircraft
            self.root.after(0, lambda a=aircraft[:45]: self.aircraft_label.config(text=a))

        alt_str = f"{int(altitude_ft):,} ft"
        vs_str = f"{vs_fpm:+d} fpm"
        self.root.after(0, lambda a=alt_str: self.alt_label.config(text=a))
        self.root.after(0, lambda v=vs_str: self.vs_label.config(text=v))

        # Takeoff detection
        if self.on_ground_prev and not on_ground and altitude_ft > 30:
            self.in_flight = True
            self.flight_start_time = time.time()
            dep_name = data.get("departure_name", "----")
            self.departure_airport = dep_name[:10] if dep_name else "----"
            self.root.after(0, lambda d=self.departure_airport: self.dep_label.config(text=d))
            self.root.after(0, lambda: self.dest_label.config(text="----"))
            self.root.after(0, lambda: self.log(f"Despegue desde {self.departure_airport} — {self.current_aircraft[:25]}"))

        # Landing detection
        if not self.on_ground_prev and on_ground and self.in_flight:
            self.in_flight = False
            td_fpm = vs_fpm
            duration = int((time.time() - self.flight_start_time) / 60) if self.flight_start_time else 0
            landing = data.get("arrival_name") or data.get("departure_name") or "----"
            landing = landing[:10] if landing else "----"

            self.root.after(0, lambda d=landing: self.dest_label.config(text=d))
            self.root.after(0, lambda t=td_fpm: self.update_touchdown(t))
            self.root.after(0, lambda la=landing, t=td_fpm: self.log(f"Aterrizaje en {la} — {t} fpm"))
            threading.Thread(target=self.save_flight, args=(duration, td_fpm, landing), daemon=True).start()

        self.on_ground_prev = on_ground

    def update_touchdown(self, fpm):
        abs_fpm = abs(fpm)
        if abs_fpm <= 180:   label, color = "PERFECTO 🏆", "#00ffaa"
        elif abs_fpm <= 300: label, color = "BUENO ✅", "#7fff00"
        elif abs_fpm <= 500: label, color = "DURO ⚠️", "#ffd700"
        elif abs_fpm <= 700: label, color = "HARD LANDING 😬", "#ff8c00"
        else:                label, color = "CRASH TREN 💥", "#ff2244"
        self.td_label.config(text=f"{fpm} fpm", fg=color)
        self.td_rating_label.config(text=label, fg=color)

    def save_flight(self, duration, td_fpm, dest):
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        flight = {
            "date": today, "airline": "", "aircraft": self.current_aircraft,
            "departure": self.departure_airport, "destination": dest,
            "duration": duration, "touchdown": str(td_fpm),
            "notes": "Registrado automaticamente por MSFS Tracker",
        }
        success = supabase_insert_flight(self.access_token, self.user_id, flight)
        if success:
            self.root.after(0, lambda: self.log("✅ Vuelo guardado en Libro de Vuelo!"))
        else:
            self.root.after(0, lambda: self.log("❌ Error al guardar el vuelo"))

    def update_timer(self):
        while self.running:
            if self.in_flight and self.flight_start_time:
                elapsed = int(time.time() - self.flight_start_time)
                h = elapsed // 3600
                m = (elapsed % 3600) // 60
                s = elapsed % 60
                self.root.after(0, lambda t=f"{h:02d}:{m:02d}:{s:02d}": self.time_label.config(text=t))
            time.sleep(1)

    def logout(self):
        self.running = False
        save_config({})
        self.config = {}
        self.access_token = None
        self.user_id = None
        self.show_login()

    def on_close(self):
        self.running = False
        self.root.destroy()

    def run(self):
        self.root.mainloop()

if __name__ == "__main__":
    app = MSFSTracker()
    app.run()
