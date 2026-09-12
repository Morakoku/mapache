#!/usr/bin/env python3
"""WhatsApp relay host-side — recibe HTTP POST del contenedor y corre hermes.exe send.

El contenedor Linux no puede ejecutar hermes.exe (Windows). Este relay corre en
el host y expone un endpoint HTTP minimo al que el contenedor llama para
enviar mensajes de WhatsApp. Asi el bridge se mueve a Docker sin perder el
envio por WhatsApp.

Uso:
    python ops/whatsapp_relay.py             # arranca en :9188
    python ops/whatsapp_relay.py --port 9999

Endpoint:
    POST /send  {"to": "whatsapp:57300...", "message": "..."}
    -> 200 {"ok": true, "hermes_output": "..."}
    -> 500 {"ok": false, "error": "..."}
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERMES = r"C:\Users\edwin\AppData\Local\hermes\bin\hermes.exe"
TIMEOUT = 90

# Limite de tasa: max N envios por minuto (cola de 60s deslizante).
_rate_lock = threading.Lock()
_rate_timestamps: list[float] = []
RATE_MAX = 10  # hermes no acepta rafagas; 10/min es conservador


def can_send() -> bool:
    """True si no se excedio el limite de tasa."""
    import time

    now = time.monotonic()
    cutoff = now - 60.0
    with _rate_lock:
        # Purgar timestamps viejos
        global _rate_timestamps
        _rate_timestamps = [t for t in _rate_timestamps if t > cutoff]
        if len(_rate_timestamps) >= RATE_MAX:
            return False
        _rate_timestamps.append(now)
        return True


def do_send(to: str, message: str) -> tuple[bool, str]:
    """Ejecuta hermes.exe send y devuelve (ok, output)."""
    try:
        proc = subprocess.run(
            [HERMES, "send", "--to", to, message],
            capture_output=True,
            text=True,
            timeout=TIMEOUT,
        )
        if proc.returncode == 0:
            return True, (proc.stdout or "").strip()
        return False, (proc.stderr or proc.stdout or f"exit={proc.returncode}").strip()
    except subprocess.TimeoutExpired:
        return False, f"timeout tras {TIMEOUT}s"
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # silenciar logs HTTP ruidosos
        sys.stderr.write("[relay] " + (fmt % args) + "\n")

    def _json(self, code: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path == "/health":
            self._json(200, {"ok": True, "hermes": HERMES})
        else:
            self._json(404, {"ok": False, "error": "use POST /send"})

    def do_POST(self) -> None:
        if self.path != "/send":
            self._json(404, {"ok": False, "error": "use POST /send"})
            return

        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b""
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            self._json(400, {"ok": False, "error": "JSON invalido"})
            return

        to = (data.get("to") or "").strip()
        message = data.get("message", "")
        if not to or not message:
            self._json(400, {"ok": False, "error": "requeridos: to, message"})
            return

        if not to.startswith("whatsapp:"):
            self._json(400, {"ok": False, "error": "to debe empezar por whatsapp:"})
            return

        if not can_send():
            self._json(429, {"ok": False, "error": "rate limit (10/min)"})
            return

        ok, output = do_send(to, message)
        if ok:
            self._json(200, {"ok": True, "hermes_output": output})
        else:
            self._json(500, {"ok": False, "error": output})


def main() -> None:
    ap = argparse.ArgumentParser(description="WhatsApp relay para contenedor Docker")
    ap.add_argument("--port", type=int, default=9188)
    ap.add_argument("--host", default="0.0.0.0")
    args = ap.parse_args()

    if not subprocess.run(["where", HERMES], capture_output=True).returncode == 0:
        print(f"ADVERTENCIA: no se encuentra {HERMES}", file=sys.stderr)

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"[whatsapp-relay] escuchando en {args.host}:{args.port}")
    print(f"[whatsapp-relay] hermes -> {HERMES}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[whatapps-relay] detenido.")


if __name__ == "__main__":
    main()
