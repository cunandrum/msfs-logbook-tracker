import tkinter as tk
from tkinter import ttk, messagebox
import threading
import time
import json
import requests
from datetime import datetime, timezone
import sys
import os

# SimConnect
try:
    from SimConnect import SimConnect, AircraftRequests, AircraftEvents
    SIMCONNECT_AVAILABLE = True
except ImportError:
    SIMCONNECT_AVAILABLE = False

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
    r = requests.post(
        f"{SUPABASE_URL}/auth/v1/token?grant_type=password",
        headers={"apikey": SUPABASE_KEY, "Content-Type": "application/json"},
        json={"email": email, "password": password},
        timeout=10
    )
    if r.status_code == 200:
        return r.json()
    return None

def supabase_insert_flight(access_token, user_id, flight):
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

class MSFSTracker:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Libro de Vuelo - Tracker")
        self.root.geometry("480x680")
        self.root.minsize(400, 600)
        self.root.resizable(True, True)
        self.root.configure(bg="#0d0f14")

        # State
        self.config = load_config()
        self.access_token = self.config.get("access_token")
        self.user_id = self.config.get("user_id")
        self.running = False
        self.connected_sim = False
        self.in_flight = False
        self.flight_start_time = None
        self.departure_airport = "----"
        self.destination_airport = "----"
        self.last_touchdown = None
        self.on_ground = True
        self.sm = None
        self.aq = None
        self.current_aircraft = ""

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
        email_entry = tk.Entry(self.frame_login, textvariable=self.email_var, font=("Courier", 13),
                               bg="#1a1d24", fg="#e8d5a3", insertbackground="#e8d5a3",
                               relief="flat", bd=8)
        email_entry.pack(fill="x", pady=(2,12), ipady=6)

        tk.Label(self.frame_login, text="CONTRASEÑA", font=("Courier", 9), fg="#888888", bg="#0d0f14", anchor="w").pack(fill="x")
        self.pass_var = tk.StringVar()
        pass_entry = tk.Entry(self.frame_login, textvariable=self.pass_var, font=("Courier", 13),
                              bg="#1a1d24", fg="#e8d5a3", insertbackground="#e8d5a3",
                              show="•", relief="flat", bd=8)
        pass_entry.pack(fill="x", pady=(2,20), ipady=6)

        self.login_status = tk.Label(self.frame_login, text="", font=("Courier", 10), fg="#ff6666", bg="#0d0f14")
        self.login_status.pack()

        btn = tk.Button(self.frame_login, text="INICIAR SESION", font=("Courier", 12, "bold"),
                        bg="#e8d5a3", fg="#0d0f14", relief="flat", bd=0, cursor="hand2",
                        command=self.do_login)
        btn.pack(fill="x", pady=(10,0), ipady=12)

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
        self.frame_tracker.pack(fill="both", expand=True, padx=20, pady=20)
        self.clear_frame(self.frame_tracker)

        # Header
        tk.Label(self.frame_tracker, text="Libro de Vuelo — Tracker", font=("Georgia", 16, "bold"),
                 fg="#e8d5a3", bg="#0d0f14").pack(pady=(10,0))

        email = self.config.get("email","")
        tk.Label(self.frame_tracker, text=email, font=("Courier", 9),
                 fg="#666666", bg="#0d0f14").pack(pady=(0,20))

        # Connection status
        status_frame = tk.Frame(self.frame_tracker, bg="#1a1d24", pady=12, padx=16)
        status_frame.pack(fill="x", pady=(0,16))
        tk.Label(status_frame, text="SIMULADOR", font=("Courier", 9), fg="#888888", bg="#1a1d24").pack(anchor="w")
        self.sim_status_label = tk.Label(status_frame, text="⬤  Desconectado", font=("Courier", 12),
                                          fg="#ff4444", bg="#1a1d24")
        self.sim_status_label.pack(anchor="w", pady=(4,0))

        # Flight info panel
        info_frame = tk.Frame(self.frame_tracker, bg="#1a1d24", pady=16, padx=16)
        info_frame.pack(fill="x", pady=(0,16))

        tk.Label(info_frame, text="VUELO EN CURSO", font=("Courier", 9), fg="#888888", bg="#1a1d24").pack(anchor="w")

        route_frame = tk.Frame(info_frame, bg="#1a1d24")
        route_frame.pack(fill="x", pady=(8,0))
        self.dep_label = tk.Label(route_frame, text="----", font=("Georgia", 28, "bold"),
                                   fg="#e8d5a3", bg="#1a1d24")
        self.dep_label.pack(side="left")
        tk.Label(route_frame, text=" → ", font=("Georgia", 22), fg="#666666", bg="#1a1d24").pack(side="left")
        self.dest_label = tk.Label(route_frame, text="----", font=("Georgia", 28, "bold"),
                                    fg="#e8d5a3", bg="#1a1d24")
        self.dest_label.pack(side="left")

        stats_frame = tk.Frame(info_frame, bg="#1a1d24")
        stats_frame.pack(fill="x", pady=(12,0))

        for label, attr in [("DURACION", "time_label"), ("ALTITUD", "alt_label"), ("V/S", "vs_label")]:
            col = tk.Frame(stats_frame, bg="#1a1d24")
            col.pack(side="left", expand=True)
            tk.Label(col, text=label, font=("Courier", 8), fg="#888888", bg="#1a1d24").pack()
            lbl = tk.Label(col, text="--:--", font=("Courier", 13, "bold"), fg="#e8d5a3", bg="#1a1d24")
            lbl.pack()
            setattr(self, attr, lbl)

        # Last touchdown
        td_frame = tk.Frame(self.frame_tracker, bg="#1a1d24", pady=12, padx=16)
        td_frame.pack(fill="x", pady=(0,16))
        tk.Label(td_frame, text="ULTIMO TOUCHDOWN", font=("Courier", 9), fg="#888888", bg="#1a1d24").pack(anchor="w")
        self.td_label = tk.Label(td_frame, text="—", font=("Georgia", 20, "bold"), fg="#e8d5a3", bg="#1a1d24")
        self.td_label.pack(anchor="w", pady=(4,0))
        self.td_rating_label = tk.Label(td_frame, text="", font=("Courier", 10), fg="#00ffaa", bg="#1a1d24")
        self.td_rating_label.pack(anchor="w")

        # Log
        log_frame = tk.Frame(self.frame_tracker, bg="#1a1d24", pady=8, padx=16)
        log_frame.pack(fill="x", pady=(0,16))
        tk.Label(log_frame, text="ACTIVIDAD", font=("Courier", 9), fg="#888888", bg="#1a1d24").pack(anchor="w")
        self.log_text = tk.Text(log_frame, height=5, font=("Courier", 9),
                                 bg="#0d0f14", fg="#666666", relief="flat", state="disabled",
                                 insertbackground="#e8d5a3")
        self.log_text.pack(fill="x", pady=(6,0))

        # Buttons
        btn_frame = tk.Frame(self.frame_tracker, bg="#0d0f14")
        btn_frame.pack(fill="x")

        self.connect_btn = tk.Button(btn_frame, text="CONECTAR A MSFS", font=("Courier", 11, "bold"),
                                      bg="#e8d5a3", fg="#0d0f14", relief="flat", bd=0, cursor="hand2",
                                      command=self.toggle_connection)
        self.connect_btn.pack(side="left", fill="x", expand=True, ipady=10, padx=(0,8))

        tk.Button(btn_frame, text="Cerrar sesion", font=("Courier", 9),
                  bg="#1a1d24", fg="#666666", relief="flat", bd=0, cursor="hand2",
                  command=self.logout).pack(side="right", ipady=10, padx=4)

    def log(self, msg):
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_text.config(state="normal")
        self.log_text.insert("end", f"[{timestamp}] {msg}\n")
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
                    if not SIMCONNECT_AVAILABLE:
                        raise Exception("SimConnect no disponible")
                    self.sm = SimConnect()
                    self.aq = AircraftRequests(self.sm, _time=2000)
                    self.connected_sim = True
                    self.root.after(0, lambda: self.sim_status_label.config(text="⬤  Conectado a MSFS", fg="#00ffaa"))
                    self.root.after(0, lambda: self.log("Conectado a MSFS 2020!"))
                except Exception as e:
                    self.root.after(0, lambda: self.sim_status_label.config(text="⬤  MSFS no encontrado...", fg="#ff8800"))
                    time.sleep(5)
                    continue

            try:
                self.read_sim_data()
                time.sleep(1)
            except Exception as e:
                self.connected_sim = False
                self.sm = None
                self.aq = None
                self.root.after(0, lambda: self.sim_status_label.config(text="⬤  Conexion perdida", fg="#ff4444"))
                self.root.after(0, lambda: self.log("Conexion perdida. Reconectando..."))

    def read_sim_data(self):
        try:
            on_ground = bool(self.aq.get("SIM_ON_GROUND"))
            altitude = self.aq.get("PLANE_ALTITUDE") or 0
            vs = self.aq.get("VERTICAL_SPEED") or 0
            vs_fpm = int(vs * 196.85)  # m/s to fpm

            # Try to get airport
            try:
                gps_wp = self.aq.get("GPS_TARGET_AIRPORT")
                if gps_wp:
                    airport = str(gps_wp).strip()
                else:
                    airport = None
            except:
                airport = None

            # Try to get aircraft name
            try:
                aircraft_title = self.aq.get("TITLE")
                if aircraft_title:
                    self.current_aircraft = str(aircraft_title).strip()
            except:
                pass

            # Update UI
            alt_str = f"{int(altitude):,} ft"
            vs_str = f"{vs_fpm:+d} fpm"
            self.root.after(0, lambda a=alt_str: self.alt_label.config(text=a))
            self.root.after(0, lambda v=vs_str: self.vs_label.config(text=v))

            # Detect takeoff
            if self.on_ground and not on_ground and altitude > 100:
                self.in_flight = True
                self.flight_start_time = time.time()
                self.departure_airport = airport or "----"
                self.root.after(0, lambda d=self.departure_airport: self.dep_label.config(text=d))
                self.root.after(0, lambda: self.dest_label.config(text="----"))
                self.root.after(0, lambda: self.log(f"Despegue detectado desde {self.departure_airport} — {self.current_aircraft or 'avion desconocido'}"))

            # Detect landing
            if not self.on_ground and on_ground and self.in_flight:
                self.in_flight = False
                landing_airport = airport or "----"
                self.destination_airport = landing_airport

                # Calculate touchdown rate
                td_fpm = vs_fpm
                self.last_touchdown = td_fpm

                # Duration
                duration = int((time.time() - self.flight_start_time) / 60) if self.flight_start_time else 0

                self.root.after(0, lambda d=landing_airport: self.dest_label.config(text=d))
                self.root.after(0, lambda t=td_fpm: self.update_touchdown(t))
                self.root.after(0, lambda: self.log(f"Aterrizaje en {landing_airport} — {td_fpm} fpm"))

                # Save flight
                threading.Thread(target=self.save_flight, args=(duration, td_fpm, landing_airport), daemon=True).start()

            self.on_ground = on_ground

            # Update destination if available
            if airport and self.in_flight:
                self.root.after(0, lambda a=airport: self.dest_label.config(text=a))

        except Exception as e:
            raise e

    def update_touchdown(self, fpm):
        abs_fpm = abs(fpm)
        if abs_fpm <= 180: label, color = "PERFECTO 🏆", "#00ffaa"
        elif abs_fpm <= 300: label, color = "BUENO ✅", "#7fff00"
        elif abs_fpm <= 500: label, color = "DURO ⚠️", "#ffd700"
        elif abs_fpm <= 700: label, color = "HARD LANDING 😬", "#ff8c00"
        else: label, color = "CRASH TREN 💥", "#ff2244"
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
        self.root.destroy()

    def run(self):
        self.root.mainloop()

if __name__ == "__main__":
    app = MSFSTracker()
    app.run()
