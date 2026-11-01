"""
BDGate webhook bridge for Render (standalone, stdlib only).

Why this exists: the shop bot runs on a VM that has no public inbound
address, so BDGate cannot POST webhooks directly to it. Deploy this
file as a Render Web Service. BDGate POSTs to the Render URL, this
service stores paid events, and the bot (on the VM) polls
GET /events every minute, credits matching pending payments
idempotently, then ACKs them via POST /events/ack.

Security: set BRIDGE_SECRET on Render, and use the same secret
  - in the BDGate webhook URL query:  https://<service>.onrender.com/webhook?secret=<BRIDGE_SECRET>
  - in the bot VM .env as WEBHOOK_BRIDGE_SECRET (poller uses it as a header)
Without the matching secret, POST /webhook and GET /events return 403.
No payment keys, bot tokens or user data are stored here -- only
order_id + amount + status, which the bot matches against its own
pending payments before crediting anything.
"""
import json
import logging
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger("bridge")

PORT = int(os.environ.get("PORT", "8080") or 8080)
SECRET = os.environ.get("BRIDGE_SECRET", "")
EVENTS_FILE = os.environ.get("BRIDGE_EVENTS_FILE", "bridge_events.json")
LOCK = threading.Lock()

PAID = ("success", "paid", "completed", "approved", "successful", "payment.success", "payment_success")


def load_events():
    try:
        with open(EVENTS_FILE, encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except Exception:
        return []


def save_events(events):
    tmp = EVENTS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(events, f, ensure_ascii=False)
    os.replace(tmp, EVENTS_FILE)


def pick(payload, keys):
    if not isinstance(payload, dict):
        return None
    for k in keys:
        if payload.get(k) is not None:
            return payload.get(k)
    data = payload.get("data")
    if isinstance(data, dict):
        for k in keys:
            if data.get(k) is not None:
                return data.get(k)
    return None


def is_paid(payload):
    st = pick(payload, ["status", "payment_status", "state", "event", "type"])
    if st is not None and str(st).lower() in PAID:
        return True
    for k in ["paid", "is_paid", "success"]:
        if pick(payload, [k]) is True:
            return True
    return False


def check_secret(handler):
    if not SECRET:
        return True
    query = parse_qs(urlparse(handler.path).query)
    q = query.get("secret", [None])[0]
    h = handler.headers.get("X-Bridge-Secret")
    return q == SECRET or h == SECRET


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        logger.info("bridge: " + fmt % args)

    def _json(self, code, obj):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except Exception:
            length = 0
        raw = self.rfile.read(length) if length > 0 else b""
        try:
            data = json.loads(raw.decode("utf-8")) if raw else {}
        except Exception:
            try:
                parsed = parse_qs(raw.decode("utf-8"))
                data = {k: v[0] for k, v in parsed.items()}
            except Exception:
                data = {}
        return data if isinstance(data, dict) else {}

    def do_GET(self):
        path = urlparse(self.path).path
        if path in ("/health", "/"):
            self._json(200, {"ok": True, "service": "bdgate-webhook-bridge"})
            return
        if path == "/events":
            if not check_secret(self):
                self._json(403, {"ok": False, "reason": "bad_secret"})
                return
            with LOCK:
                events = load_events()
            self._json(200, {"ok": True, "events": events})
            return
        self._json(404, {"ok": False})

    def do_POST(self):
        path = urlparse(self.path).path
        if path in ("/webhook", "/bdgate-webhook"):
            if not check_secret(self):
                self._json(403, {"ok": False, "reason": "bad_secret"})
                return
            payload = self._body()
            if not is_paid(payload):
                self._json(200, {"ok": False, "reason": "not_paid_status"})
                return
            order_id = pick(payload, ["order_id", "merchant_order_id", "trx_id", "payment_id", "reference", "invoice_id", "slug"])
            if not order_id:
                self._json(200, {"ok": False, "reason": "no_order_id"})
                return
            amt_raw = pick(payload, ["amount", "paid_amount", "total", "total_amount"])
            try:
                amount = float(amt_raw) if amt_raw is not None else None
            except Exception:
                amount = None
            with LOCK:
                events = load_events()
                for ev in events:
                    if ev.get("order_id") == str(order_id):
                        self._json(200, {"ok": True, "duplicate": True, "event_id": ev.get("id")})
                        return
                new_id = (max([e.get("id", 0) for e in events], default=0) + 1)
                events.append({"id": new_id, "order_id": str(order_id), "amount": amount})
                save_events(events)
            logger.info(f"stored webhook event {new_id} order {order_id} amount {amount}")
            self._json(200, {"ok": True, "event_id": new_id})
            return
        if path == "/events/ack":
            if not check_secret(self):
                self._json(403, {"ok": False, "reason": "bad_secret"})
                return
            body = self._body()
            ids = body.get("ids") or []
            order_id = body.get("order_id")
            with LOCK:
                events = load_events()
                before = len(events)
                if ids:
                    ids = set(int(i) for i in ids)
                    events = [e for e in events if e.get("id") not in ids]
                elif order_id:
                    events = [e for e in events if e.get("order_id") != str(order_id)]
                save_events(events)
            self._json(200, {"ok": True, "acked": before - len(events)})
            return
        self._json(404, {"ok": False})


if __name__ == "__main__":
    if not SECRET:
        logger.warning("BRIDGE_SECRET is not set -- set it on Render before using this in production.")
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    logger.info(f"BDGate webhook bridge listening on port {PORT}")
    server.serve_forever()
