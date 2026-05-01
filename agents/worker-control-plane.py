from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from datetime import datetime, timezone
import importlib.util


def module_exists(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


class JsonHandler(BaseHTTPRequestHandler):
    service_name = "worker"
    port = 0

    def _send(self, status: int, body: dict) -> None:
        payload = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args) -> None:
        return

    def do_GET(self) -> None:
        if self.path != "/health":
            self._send(404, {"error": "not_found"})
            return
        now = datetime.now(timezone.utc).isoformat()
        if self.service_name == "diffusion":
            torch_ok = module_exists("torch")
            diffusers_ok = module_exists("diffusers")
            self._send(200, {
                "service": "diffusion-worker-control",
                "port": self.port,
                "status": "prepared" if not (torch_ok and diffusers_ok) else "ready",
                "real_model_loaded": False,
                "missing": [m for m, ok in (("torch", torch_ok), ("diffusers", diffusers_ok)) if not ok],
                "ts": now,
            })
        else:
            self._send(200, {
                "service": "post-processor-control",
                "port": self.port,
                "status": "ready",
                "mode": "ffmpeg-plan",
                "ts": now,
            })

    def do_POST(self) -> None:
        if self.path not in ("/generate", "/process"):
            self._send(404, {"error": "not_found"})
            return
        self._send(503 if self.service_name == "diffusion" else 202, {
            "service": self.service_name,
            "accepted": self.service_name != "diffusion",
            "status": "deps_missing" if self.service_name == "diffusion" else "queued_for_future_worker",
            "message": "GPU diffusion requires torch/diffusers install and model cache." if self.service_name == "diffusion" else "Post-process control endpoint is alive; real FFmpeg wiring remains manual.",
        })


def make_handler(name: str, listen_port: int):
    class Handler(JsonHandler):
        service_name = name
        port = listen_port
    return Handler


def serve(name: str, port: int) -> None:
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(name, port))
    print(f"[WorkerControl] {name} listening on 127.0.0.1:{port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    threads = [
        threading.Thread(target=serve, args=("diffusion", 8081), daemon=True),
        threading.Thread(target=serve, args=("postprocessor", 8082), daemon=True),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
