import tkinter as tk
from tkinter import ttk
import threading
import time
import json
import requests
from datetime import datetime, timezone
import os
import struct

# Try FSUIPC first, then SimConnect
FSUIPC_AVAILABLE = False
SIMCONNECT_AVAILABLE = False

try:
    import pyuipc
    FSUIPC_AVAILABLE = True
except ImportError:
    pass

if not FSUIPC_AVAILABLE:
    try:
        from SimConnect import SimConnect, AircraftRequests
        SIMCONNECT_AVAILABLE = True
    except ImportError:
        pass

SUPABASE_URL = "https://xsvnxexbkrpnwfxhfoho.supabase.co"
SUPABASE_KEY = "sb_publishable_5VQjgvOKq1k_wgpLegG1hA_AGj15Bq_"
CONFIG_FILE = os.path.join(os.path.expanduser("~"), ".msfs_logbook_config.json")

# FSUIPC offsets
OFFSET_ON_GROUND    = 0x0366  # 2 bytes - on ground flag
OFFSET_ALTITUDE     = 0x0574  # 8 bytes - altitude in meters * 65536
OFFSET_VS           = 0x02C8  # 4 bytes - vertical speed ft/min * 256
OFFSET_ACFT_TITLE   = 0x3D00  # 256 bytes - aircraft title string
OFFSET_ICAO         = 0x0BB8  # 4 bytes - nearest airport ICAO (not reliable, use GPS)
OFFSET_LAT          = 0x0560  # 8 bytes - latitude
OFFSET_LON          = 0x0568  # 8 bytes - longitude
OFFSET_AIRPORT      = 0x0B4C  # Not standard, use GPS approach airport
OFFSET_GPS_AIRPORT  = 0x6FB0  # 8 bytes - GPS destination airport (ICAO)

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
            json={"email": email, "password": password},
            timeout=10
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
            json={**flight, "pilot_id": user_id},
            timeout=10
        )
        return r.status_code in (200, 201)
    except:
        return False

def read_fsuipc_string(offset, length=64):
    try:
        data = pyuipc.read([(offset, pyuipc.TYPE_STRING.replace("s", f"{length}s"))])
        return data[0].decode("latin-1").rstrip("\x00").strip()
    except:
        return ""

class MSFSTracker:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Libro de Vuelo - Tracker")
        self.root.geometry("480x680")
        self.root.minsize(440, 580)
        self.root.resizable(True, True)
        self.root.configure(bg="#0d0f14")

        self.config = load_config()
        self.access_token = self.config.get("access_token")
        self.user_id = self.config.get("user_id")
        self.running = False
        self.connected_sim = False
        self.in_flight = False
        self.flight_start_time = None
        self.departure_airport = "----"
        self.current_aircraft = ""
        self.on_ground_prev = True
        self.sm = None
        self.aq = None

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
        tk.Label(self.frame_login, text="Libro de Vuelo", font=("Georgia", 22, "bold"), fg="#e8d5a3", bg="#0d0f14").pack(pady=(0,30))
        tk.Label(self.frame_login, text="Tracker de vuelos automatico", font=("Courier", 11), fg="#888888", bg="#0d0f14").pack(pady=(0,30))

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
                 fg="#666666", bg="#0d0f14").pack(pady=(0,12))

        # Mode indicator
        mode = "FSUIPC7" if FSUIPC_AVAILABLE else "SimConnect" if SIMCONNECT_AVAILABLE else "Sin libreria"
        tk.Label(self.frame_tracker, text=f"Modo: {mode}", font=("Courier", 8),
                 fg="#444", bg="#0d0f14").pack()

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

        # Aircraft label
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
            if not self.connected_sim:
                try:
                    if FSUIPC_AVAILABLE:
                        pyuipc.open(pyuipc.SIM_ANY)
                        self.connected_sim = True
                        self.root.after(0, lambda: self.sim_status_label.config(text="⬤  Conectado via FSUIPC7", fg="#00ffaa"))
                        self.root.after(0, lambda: self.log("Conectado a MSFS via FSUIPC7!"))
                    elif SIMCONNECT_AVAILABLE:
                        self.sm = SimConnect()
                        self.aq = AircraftRequests(self.sm, _time=2000)
                        self.connected_sim = True
                        self.root.after(0, lambda: self.sim_status_label.config(text="⬤  Conectado via SimConnect", fg="#00ffaa"))
                        self.root.after(0, lambda: self.log("Conectado a MSFS via SimConnect!"))
                    else:
                        self.root.after(0, lambda: self.sim_status_label.config(text="⬤  Sin libreria SimConnect/FSUIPC", fg="#ff4444"))
                        self.running = False
                        return
                except Exception as e:
                    self.root.after(0, lambda: self.sim_status_label.config(text="⬤  MSFS no encontrado...", fg="#ff8800"))
                    time.sleep(5)
                    continue

            try:
                self.read_sim_data()
                time.sleep(1)
            except Exception as e:
                self.connected_sim = False
                if FSUIPC_AVAILABLE:
                    try: pyuipc.close()
                    except: pass
                self.root.after(0, lambda: self.sim_status_label.config(text="⬤  Conexion perdida", fg="#ff4444"))
                self.root.after(0, lambda: self.log("Conexion perdida. Reconectando..."))
                time.sleep(3)

    def read_sim_data(self):
        if FSUIPC_AVAILABLE:
            self.read_fsuipc()
        elif SIMCONNECT_AVAILABLE:
            self.read_simconnect()

    def read_fsuipc(self):
        try:
            results = pyuipc.read([
                (0x0366, "H"),   # on ground (0=air, 1=ground)
                (0x3324, "d"),   # altitude feet * 65536
                (0x02C8, "d"),   # VS ft/min * 256
                (0x3D00, "128s"), # aircraft title
            ])

            on_ground = bool(results[0])
            altitude = results[1] / 65536.0
            vs_fpm = int(results[2] / 256.0)
            acft_raw = results[3]
            aircraft = acft_raw.decode("latin-1").rstrip("\x00").strip() if acft_raw else ""

            if aircraft:
                self.current_aircraft = aircraft
                self.root.after(0, lambda a=aircraft[:40]: self.aircraft_label.config(text=a))

            alt_str = f"{int(altitude):,} ft"
            vs_str = f"{vs_fpm:+d} fpm"
            self.root.after(0, lambda a=alt_str: self.alt_label.config(text=a))
            self.root.after(0, lambda v=vs_str: self.vs_label.config(text=v))

            # Takeoff detection
            if self.on_ground_prev and not on_ground and altitude > 50:
                self.in_flight = True
                self.flight_start_time = time.time()
                # Get nearest airport as departure
                try:
                    icao_data = pyuipc.read([(0x0658, "4s")])
                    icao = icao_data[0].decode("latin-1").rstrip("\x00").strip()
                    self.departure_airport = icao if icao else "----"
                except:
                    self.departure_airport = "SADP"  # fallback

                self.root.after(0, lambda d=self.departure_airport: self.dep_label.config(text=d))
                self.root.after(0, lambda: self.dest_label.config(text="----"))
                self.root.after(0, lambda: self.log(f"Despegue desde {self.departure_airport} — {self.current_aircraft[:30]}"))

            # Landing detection
            if not self.on_ground_prev and on_ground and self.in_flight and altitude < 100:
                self.in_flight = False
                td_fpm = vs_fpm

                # Get landing airport
                try:
                    icao_data = pyuipc.read([(0x0658, "4s")])
                    icao = icao_data[0].decode("latin-1").rstrip("\x00").strip()
                    landing_airport = icao if icao else "----"
                except:
                    landing_airport = "----"

                duration = int((time.time() - self.flight_start_time) / 60) if self.flight_start_time else 0

                self.root.after(0, lambda d=landing_airport: self.dest_label.config(text=d))
                self.root.after(0, lambda t=td_fpm: self.update_touchdown(t))
                self.root.after(0, lambda la=landing_airport, t=td_fpm: self.log(f"Aterrizaje en {la} — {t} fpm"))

                threading.Thread(target=self.save_flight, args=(duration, td_fpm, landing_airport), daemon=True).start()

            self.on_ground_prev = on_ground

        except Exception as e:
            raise e

    def read_simconnect(self):
        try:
            on_ground = bool(self.aq.get("SIM_ON_GROUND"))
            altitude = self.aq.get("PLANE_ALTITUDE") or 0
            vs = self.aq.get("VERTICAL_SPEED") or 0
            vs_fpm = int(vs * 196.85)

            try:
                aircraft = str(self.aq.get("TITLE") or "").strip()
                if aircraft:
                    self.current_aircraft = aircraft
                    self.root.after(0, lambda a=aircraft[:40]: self.aircraft_label.config(text=a))
            except:
                pass

            alt_str = f"{int(altitude):,} ft"
            vs_str = f"{vs_fpm:+d} fpm"
            self.root.after(0, lambda a=alt_str: self.alt_label.config(text=a))
            self.root.after(0, lambda v=vs_str: self.vs_label.config(text=v))

            if self.on_ground_prev and not on_ground and altitude > 100:
                self.in_flight = True
                self.flight_start_time = time.time()
                self.departure_airport = "----"
                self.root.after(0, lambda: self.dep_label.config(text="----"))
                self.root.after(0, lambda: self.dest_label.config(text="----"))
                self.root.after(0, lambda: self.log(f"Despegue detectado — {self.current_aircraft[:30]}"))

            if not self.on_ground_prev and on_ground and self.in_flight:
                self.in_flight = False
                td_fpm = vs_fpm
                duration = int((time.time() - self.flight_start_time) / 60) if self.flight_start_time else 0

                self.root.after(0, lambda t=td_fpm: self.update_touchdown(t))
                self.root.after(0, lambda: self.log(f"Aterrizaje — {td_fpm} fpm"))
                threading.Thread(target=self.save_flight, args=(duration, td_fpm, "----"), daemon=True).start()

            self.on_ground_prev = on_ground
        except Exception as e:
            raise e

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
            "date": today,
            "airline": "",
            "aircraft": self.current_aircraft,
            "departure": self.departure_airport,
            "destination": dest,
            "duration": duration,
            "touchdown": str(td_fpm),
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
                time_str = f"{h:02d}:{m:02d}:{s:02d}"
                self.root.after(0, lambda t=time_str: self.time_label.config(text=t))
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
        if FSUIPC_AVAILABLE and self.connected_sim:
            try: pyuipc.close()
            except: pass
        self.root.destroy()

    def run(self):
        self.root.mainloop()

if __name__ == "__main__":
    app = MSFSTracker()
    app.run()
