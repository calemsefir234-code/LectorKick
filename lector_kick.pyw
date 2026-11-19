import json
import queue
import re
import threading
import time
import uuid
import tkinter as tk
from tkinter import ttk, messagebox

import pyttsx3
import requests
import websocket


BASE = "https://web.kick.com/api/v1/realtime"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131 Safari/537.36",
    "Origin": "https://kick.com",
    "Referer": "https://kick.com/",
}


def get_channel_ids(slug):
    response = requests.get(f"https://kick.com/api/v2/channels/{slug}", headers=HEADERS, timeout=20)
    response.raise_for_status()
    data = response.json()
    channel_id = data.get("id")
    chatroom_id = (data.get("chatroom") or {}).get("id")
    if not chatroom_id:
        response = requests.get(f"https://kick.com/api/v2/channels/{slug}/chatroom", headers=HEADERS, timeout=20)
        response.raise_for_status()
        room = response.json()
        chatroom_id = room.get("id") or (room.get("data") or {}).get("id")
        channel_id = channel_id or room.get("channel_id")
    if not chatroom_id:
        raise RuntimeError("No encontré el chat. Revisa el nombre del canal.")
    return channel_id, chatroom_id


def connect_realtime(channel_id, client_id):
    body = {
        "client": {"id": client_id, "type": "web"},
        "capabilities": {"accepted_providers": [{"provider": "centrifugo"}, {"provider": "pusher"}]},
    }
    endpoint = f"{BASE}/channels/{channel_id}/chat/connection" if channel_id else f"{BASE}/connection"
    response = requests.post(endpoint, json=body, headers=HEADERS, timeout=20)
    response.raise_for_status()
    choices = response.json().get("data", {}).get("connections", [])
    for choice in choices:
        if choice.get("provider") == "centrifugo":
            return choice["credentials"]["url"]
    raise RuntimeError("Kick no ofreció una conexión compatible para el chat.")


def get_guest_token(client_id):
    response = requests.post(f"{BASE}/auth/connection", json={"client_id": client_id}, headers=HEADERS, timeout=20)
    response.raise_for_status()
    return response.json()["data"]["token"]


def chat_message(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return None
    if not isinstance(value, dict):
        return None
    if "content" in value:
        # Kick sends emotes inline as [emote:<id>:<name>]; strip the token
        # before displaying or sending the message to text-to-speech.
        text = re.sub(r"\[emote:\d+:[^\]]+\]", " ", str(value.get("content", "")), flags=re.IGNORECASE)
        text = re.sub(r"\s+", " ", text).strip()
        sender = value.get("sender") or {}
        name = sender.get("username") or sender.get("slug") or "Alguien"
        return (name, text) if text else None
    for key in ("data", "message", "payload"):
        found = chat_message(value.get(key))
        if found:
            return found
    return None


class KickReaderApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Lector de chat de Kick")
        self.root.geometry("620x500")
        self.root.minsize(520, 400)
        self.root.configure(bg="#111318")
        self.events = queue.Queue()
        self.speech_queue = queue.Queue()
        self.stop_event = threading.Event()
        self.reader_thread = None
        self.speaker_thread = threading.Thread(target=self.speech_worker, daemon=True)
        self.speaker_thread.start()
        self.build_ui()
        self.root.after(100, self.process_events)
        self.root.protocol("WM_DELETE_WINDOW", self.close)

    def build_ui(self):
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TFrame", background="#111318")
        style.configure("TLabel", background="#111318", foreground="#f1f2f4", font=("Segoe UI", 10))
        style.configure("Title.TLabel", font=("Segoe UI", 20, "bold"), foreground="#53e0a1")
        style.configure("Muted.TLabel", foreground="#aeb3be")
        style.configure("TButton", font=("Segoe UI", 10, "bold"), padding=(12, 8))
        style.configure("TCheckbutton", background="#111318", foreground="#f1f2f4", font=("Segoe UI", 10))

        outer = ttk.Frame(self.root, padding=20)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="Lector de Kick", style="Title.TLabel").pack(anchor="w")
        ttk.Label(outer, text="Escucha en voz alta los mensajes nuevos de un chat en vivo.", style="Muted.TLabel").pack(anchor="w", pady=(2, 18))

        row = ttk.Frame(outer)
        row.pack(fill="x")
        ttk.Label(row, text="Canal").pack(side="left", padx=(0, 10))
        self.channel = tk.StringVar()
        self.channel_entry = ttk.Entry(row, textvariable=self.channel, font=("Segoe UI", 11))
        self.channel_entry.pack(side="left", fill="x", expand=True, ipady=5)
        self.start_button = ttk.Button(row, text="Iniciar", command=self.start)
        self.start_button.pack(side="left", padx=(10, 0))
        self.stop_button = ttk.Button(row, text="Detener", command=self.stop, state="disabled")
        self.stop_button.pack(side="left", padx=(8, 0))

        self.read_aloud = tk.BooleanVar(value=True)
        ttk.Checkbutton(outer, text="Leer mensajes en voz alta", variable=self.read_aloud).pack(anchor="w", pady=(14, 8))
        self.status = tk.StringVar(value="Listo. Escribe el nombre del canal para comenzar.")
        ttk.Label(outer, textvariable=self.status, style="Muted.TLabel").pack(anchor="w", pady=(0, 8))

        ttk.Label(outer, text="Mensajes recientes").pack(anchor="w", pady=(4, 6))
        box = tk.Frame(outer, bg="#1b1e25", highlightbackground="#30343e", highlightthickness=1)
        box.pack(fill="both", expand=True)
        self.log = tk.Text(box, bg="#1b1e25", fg="#f1f2f4", insertbackground="white", relief="flat", wrap="word", font=("Segoe UI", 10), padx=12, pady=10, state="disabled")
        scrollbar = ttk.Scrollbar(box, orient="vertical", command=self.log.yview)
        self.log.configure(yscrollcommand=scrollbar.set)
        self.log.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        ttk.Label(outer, text="El lector solo escucha mensajes; no inicia sesión ni escribe en el chat.", style="Muted.TLabel").pack(anchor="w", pady=(10, 0))

    def start(self):
        channel = self.channel.get().strip().lstrip("@").lower()
        if not channel:
            messagebox.showinfo("Falta el canal", "Escribe el nombre de un canal de Kick.")
            return
        self.channel.set(channel)
        self.stop_event.clear()
        self.start_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.status.set(f"Conectando con {channel}…")
        self.add_log(f"Conectando con el canal {channel}…")
        self.reader_thread = threading.Thread(target=self.reader_worker, args=(channel,), daemon=True)
        self.reader_thread.start()

    def stop(self):
        self.stop_event.set()
        self.status.set("Deteniendo…")
        self.start_button.configure(state="normal")
        self.stop_button.configure(state="disabled")

    def reader_worker(self, channel):
        try:
            channel_id, room_id = get_channel_ids(channel)
            self.events.put(("status", f"Conectando al chat de {channel}…"))
            client_id = str(uuid.uuid4())
            delay = 2
            while not self.stop_event.is_set():
                ws = None
                try:
                    url = connect_realtime(channel_id, client_id)
                    token = get_guest_token(client_id)
                    ws = websocket.create_connection(url, timeout=4, origin="https://kick.com", header=["User-Agent: " + HEADERS["User-Agent"]])
                    ws.settimeout(2)
                    ws.send(json.dumps({"connect": {"token": token, "name": "js"}, "id": 1}))
                    raw = ws.recv()
                    if not raw:
                        raise RuntimeError("Kick cerró la conexión antes de confirmar el acceso.")
                    reply = json.loads(raw)
                    if "error" in reply:
                        raise RuntimeError(f"Kick rechazó la conexión: {reply['error']}")
                    if "connect" not in reply:
                        raise RuntimeError(f"Kick no confirmó la conexión: {reply}")

                    ws.send(json.dumps({"subscribe": {"channel": f"chatrooms.{room_id}.v2"}, "id": 2}))
                    subscribed = False
                    subscribe_deadline = time.monotonic() + 12
                    while not self.stop_event.is_set() and time.monotonic() < subscribe_deadline:
                        try:
                            raw = ws.recv()
                        except websocket.WebSocketTimeoutException:
                            continue
                        if not raw:
                            raise RuntimeError("Kick cerró la conexión mientras confirmaba el chat.")
                        packet = json.loads(raw)
                        if "error" in packet:
                            raise RuntimeError(f"Kick rechazó la suscripción al chat: {packet['error']}")
                        if packet.get("id") == 2:
                            if "subscribe" not in packet:
                                raise RuntimeError(f"Kick no aceptó la suscripción: {packet}")
                            subscribed = True
                            break
                        if packet == {}:
                            ws.send("{}")
                            continue
                        push = packet.get("push", {})
                        if "ping" in push:
                            ws.send(json.dumps({"pong": {}}))
                        elif "pub" in push:
                            message = chat_message(push["pub"].get("data"))
                            if message:
                                self.events.put(("message", message))
                    if self.stop_event.is_set():
                        break
                    if not subscribed:
                        raise RuntimeError("Kick no confirmó la suscripción al chat en 12 segundos.")

                    connected_at = time.monotonic()
                    self.events.put(("status", f"Conectado a {channel}. Leyendo mensajes nuevos."))
                    self.events.put(("log", f"Conectado al chat de {channel}."))
                    while not self.stop_event.is_set():
                        try:
                            raw = ws.recv()
                        except websocket.WebSocketTimeoutException:
                            continue
                        if not raw:
                            raise RuntimeError("Kick cerró el WebSocket (respuesta vacía).")
                        try:
                            packet = json.loads(raw)
                        except json.JSONDecodeError as exc:
                            raise RuntimeError(f"Kick envió una respuesta no válida: {raw[:80]!r}") from exc
                        if packet == {}:
                            ws.send("{}")
                            continue
                        push = packet.get("push", {})
                        if "ping" in push:
                            ws.send(json.dumps({"pong": {}}))
                        elif "pub" in push:
                            message = chat_message(push["pub"].get("data"))
                            if message:
                                self.events.put(("message", message))
                        if time.monotonic() - connected_at >= 30:
                            delay = 2
                except Exception as exc:
                    if not self.stop_event.is_set():
                        self.events.put(("status", f"Se perdió la conexión. Reintentando en {delay} s…"))
                        self.events.put(("log", f"Conexión interrumpida: {exc}"))
                        self.stop_event.wait(delay)
                        delay = min(delay * 2, 30)
                finally:
                    if ws is not None:
                        try:
                            ws.close()
                        except Exception:
                            pass
        except Exception as exc:
            self.events.put(("error", str(exc)))
        finally:
            self.events.put(("stopped", None))

    def speech_worker(self):
        engine = None
        while True:
            item = self.speech_queue.get()
            if item is None:
                return
            try:
                if engine is None:
                    engine = pyttsx3.init()
                    engine.setProperty("rate", 175)
                engine.say(item)
                engine.runAndWait()
            except Exception as exc:
                self.events.put(("log", f"No se pudo reproducir la voz: {exc}"))

    def add_log(self, text):
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        lines = int(self.log.index("end-1c").split(".")[0])
        if lines > 250:
            self.log.delete("1.0", f"{lines - 250}.0")
        self.log.see("end")
        self.log.configure(state="disabled")

    def process_events(self):
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == "status":
                    self.status.set(value)
                elif kind == "log":
                    self.add_log(value)
                elif kind == "message":
                    name, text = value
                    self.add_log(f"{name}: {text}")
                    if self.read_aloud.get():
                        self.speech_queue.put(f"{name} dice: {text}")
                elif kind == "error":
                    self.status.set("No se pudo iniciar el lector.")
                    messagebox.showerror("Error al conectar", value)
                elif kind == "stopped":
                    self.start_button.configure(state="normal")
                    self.stop_button.configure(state="disabled")
                    if self.stop_event.is_set():
                        self.status.set("Lector detenido.")
        except queue.Empty:
            pass
        self.root.after(100, self.process_events)

    def close(self):
        self.stop_event.set()
        self.speech_queue.put(None)
        self.root.destroy()


if __name__ == "__main__":
    window = tk.Tk()
    app = KickReaderApp(window)
    window.mainloop()
