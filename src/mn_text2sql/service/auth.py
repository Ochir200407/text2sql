"""Вэб үйлчилгээний хандалтын хяналт (docs/research/R4_security_governance.md — A1, A3, A4, A5, A6).

Бодлого (service.mode-оор):
* `dev` / `local` горим — T2S_WEB_KEY тохируулаагүй бол нээлттэй (локал хөгжүүлэлт, хуучин зан төлөв).
* `shadow` / `pilot` / `live` / `prod` г.м. бусад горим — **secure by default**:
  - T2S_WEB_KEY байвал түлхүүр заавал (header `X-T2S-Key` эсвэл хуучин `?k=`), `hmac.compare_digest`.
  - T2S_WEB_KEY байхгүй бол зөвхөн localhost-оос шууд (proxy/tunnel-ийн толгойгүй) ирсэн хүсэлтийг зөвшөөрнө;
    бусдад 403. `serve()` мөн 0.0.0.0-ийн оронд 127.0.0.1-д холбогдоно (чанга анхааруулгатай).
  - T2S_INSECURE_OPEN=1 — дээрхийг ухамсартайгаар унтраах (зөвхөн synthetic демо; лог-д анхааруулна).
* Админ үйлдэл (flow хадгалах/устгах, серверийн лог, verified SQL) — тусдаа T2S_ADMIN_KEY
  (header `X-T2S-Admin` эсвэл `?admin=`); тохируулаагүй бол хаалттай.
* Түлхүүргүй (dev биш) горимд localhost-оос ирсэн хүсэлтийн `Host` толгой итгэмжлэгдсэн байх ёстой
  (localhost, 127.0.0.1, [::1] + `service.trusted_hosts` / `T2S_TRUSTED_HOSTS`) — DNS rebinding-ээс (хөтөч халдагчийн
  домэйныг Host-д илгээнэ; 2-р аудит S2). Ижил машины хөтөч (http://localhost:8080, http://127.0.0.1:8080) хэвээр.
  **Энэ нь port forwarding-оос ХАМГААЛАХГҮЙ** (3-р аудит L4): түүхий TCP forwarder (ssh -R/-L, socat, ngrok tcp,
  kubectl port-forward) нь холболтыг 127.0.0.1-ээс, proxy-ийн толгойгүй дамжуулдаг бөгөөд гадны клиент `Host:
  localhost` гэж илгээх нь хангалттай. Портыг өөр машинаас хүрэх боломжтой болгох БҮХ тохиолдолд T2S_WEB_KEY заавал.
* `service.mode`-ийг нэг хэлбэрт (`norm_mode`/`service_mode`/`is_shadow`) — харьцуулалт бүр ижил (S5).
* `WorkSlots` — pipeline ажиллуулах, SQL гүйцэтгэх бүх endpoint-д нэг хуваалцсан зэрэг ажиллах хязгаар (S1, R4 R1).
* `RateLimit` — хямд endpoint-ийн (/flow/compile, /feedback) нийт хурдны хязгаар; `BodySizeLimit` — хүсэлтийн биеийн
  дээд хэмжээ (ASGI middleware, 413) (3-р аудит H1, L6).

Header-ийг endpoint бүрийн гарын үсгийг өөрчлөхгүйгээр уншихын тулд router-ийн async dependency (`capture_request`)
хүсэлтийн толгой ба клиентийн хаягийг contextvar-д хадгална (sync endpoint-ийн threadpool context-д хуулагдана).
"""

# (from __future__ annotations-гүй: FastAPI dependency-ийн Request annotation-ийг шууд шийдэх ёстой)

import hmac
import logging
import os
import threading
from contextlib import contextmanager
from contextvars import ContextVar

log = logging.getLogger(__name__)

DEV_MODES = frozenset({"dev", "local"})
KEY_HEADER = "x-t2s-key"
ADMIN_HEADER = "x-t2s-admin"
# Эдгээрийн аль нэг байвал хүсэлт reverse proxy / Cloudflare tunnel-ээр ирсэн (localhost ч гэсэн гадных)
PROXY_HEADERS = ("x-forwarded-for", "forwarded", "cf-connecting-ip", "x-real-ip", "cf-ray")
LOOPBACK = frozenset({"127.0.0.1", "::1", "localhost"})
# Түлхүүргүй горимд зөвшөөрөх Host толгой (порт хассан): ижил машины хөтөч эдгээрийн аль нэгийг илгээнэ
TRUSTED_HOSTS = frozenset({"localhost", "127.0.0.1", "[::1]"})
TRUSTED_HOSTS_ENV = "T2S_TRUSTED_HOSTS"

_REQ: ContextVar[dict | None] = ContextVar("t2s_request_auth", default=None)


def capture_dependency():
    """APIRouter/FastAPI-ийн `dependencies=[...]`-д өгөх Depends(...): хүсэлтийн түлхүүрийн толгой ба клиентийн
    хаягийг contextvar-д хадгална. Async тул хүсэлтийн task-ийн context-д бичигдэж, sync endpoint-ийн thread-д хуулагдана."""
    from fastapi import Depends, Request

    async def _capture(request: Request) -> None:
        h = request.headers
        _REQ.set({"key": h.get(KEY_HEADER), "admin": h.get(ADMIN_HEADER),
                  "client": request.client.host if request.client else None,
                  "host": h.get("host"),
                  "proxied": any(p in h for p in PROXY_HEADERS)})
    return Depends(_capture)


def current() -> dict:
    return _REQ.get() or {}


def header_key() -> str | None:
    return current().get("key")


def header_admin() -> str | None:
    return current().get("admin")


def host_name(host: str | None) -> str:
    """Host толгойн хостын хэсэг (порт, төгсгөлийн цэггүй, жижиг үсгээр): 'LocalHost:8080' → 'localhost',
    '[::1]:8080' → '[::1]', '::1' → '[::1]'. Хоосон бол ''."""
    h = (host or "").strip().lower()
    if h.startswith("["):
        end = h.find("]")
        return h[:end + 1] if end > 0 else h
    if h.count(":") > 1:          # хаалтгүй IPv6 (стандарт бус) — хаалттай хэлбэрт
        return f"[{h}]"
    return h.split(":", 1)[0].rstrip(".")


def trusted_hosts(extra=None) -> frozenset:
    """TRUSTED_HOSTS + тохиргооны жагсаалт (`service.trusted_hosts`: list эсвэл таслалтай мөр) + T2S_TRUSTED_HOSTS."""
    items: list[str] = []
    for src in (extra, os.environ.get(TRUSTED_HOSTS_ENV)):
        if not src:
            continue
        items += src.split(",") if isinstance(src, str) else [str(x) for x in src]
    return TRUSTED_HOSTS | {host_name(x) for x in items if host_name(x)}


def is_local_direct(trusted: frozenset | None = None) -> bool:
    """Хүсэлт localhost-оос шууд (proxy/tunnel-ийн толгойгүй) ирсэн бөгөөд Host толгой нь итгэмжлэгдсэн эсэх
    (S2: DNS rebinding — халдагчийн домэйн 127.0.0.1 рүү заасан ч хөтөч `Host: evil.example` илгээнэ).
    Хязгаар (L4): түүхий TCP forwarder (ssh -R, socat) дамжуулсан гадны клиент `Host: localhost` илгээвэл энэ шалгалт
    үнэн гарна — forwarder-ийг ялгах боломжгүй тул гаднаас хүрэх порт бүрт T2S_WEB_KEY шаардлагатай."""
    c = current()
    return (bool(c) and c.get("client") in LOOPBACK and not c.get("proxied")
            and host_name(c.get("host")) in (trusted if trusted is not None else trusted_hosts()))


_WARNED_HOSTS: set[str] = set()


def _warn_untrusted_host(host: str | None, mode: str | None) -> None:
    """Итгэмжлэгдээгүй Host-ыг чанга лог хийнэ (Host бүрийг нэг удаа WARNING — лог дүүргэхгүй)."""
    h = (host or "")[:100].encode("ascii", "backslashreplace").decode("ascii")
    if h in _WARNED_HOSTS:
        log.debug("Итгэмжлэгдээгүй Host %r (дахин) — 403", h)
        return
    if len(_WARNED_HOSTS) < 256:
        _WARNED_HOSTS.add(h)
    log.warning("!!! T2S_WEB_KEY-гүй ('%s' горим): localhost-оос ирсэн хүсэлтийн Host %r итгэмжлэгдээгүй — DNS rebinding "
                "эсвэл TCP forwarder байж болзошгүй, 403. Зөвшөөрөх бол %s-д нэмнэ (эсвэл T2S_WEB_KEY тохируулна).",
                mode, h, TRUSTED_HOSTS_ENV)


def _eq(a: str | None, b: str | None) -> bool:
    return bool(a and b) and hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


def norm_mode(mode: str | None) -> str:
    """service.mode-ийн нэг хэлбэр (S5): хоосон зай хасч жижиг үсгээр ('Shadow ' → 'shadow'); None → ''."""
    return str(mode or "").strip().lower()


def service_mode(cfg) -> str:
    """Тохиргооны service.mode — нормчилсон; байхгүй/хоосон бол анхдагч 'shadow' (хамгийн хаалттай)."""
    svc = ((cfg or {}).get("service") or {}) if cfg is not None else {}
    return norm_mode(svc.get("mode")) or "shadow"


def is_shadow(mode: str | None) -> bool:
    return norm_mode(mode) == "shadow"


def is_dev(mode: str | None) -> bool:
    return norm_mode(mode) in DEV_MODES


def insecure_open() -> bool:
    return os.environ.get("T2S_INSECURE_OPEN", "").strip() in ("1", "true", "yes")


def make_check_key(web_key: str | None, mode: str | None, trusted_hosts_extra=None):
    """check_key(k) — `k` нь хуучин `?k=` query; байхгүй бол `X-T2S-Key` толгойг ашиглана.
    trusted_hosts_extra: түлхүүргүй горимд нэмж зөвшөөрөх Host-ууд (`service.trusted_hosts`)."""
    from fastapi import HTTPException

    trusted = trusted_hosts(trusted_hosts_extra)
    if not web_key and not is_dev(mode):
        if insecure_open():
            log.warning("АНХААР: T2S_WEB_KEY тохируулаагүй, T2S_INSECURE_OPEN=1 — '%s' горимд бүх endpoint нээлттэй!", mode)
        else:
            log.warning("!!! T2S_WEB_KEY тохируулаагүй ('%s' горим) — зөвхөн localhost-оос ШУУД (proxy/tunnel-ийн толгойгүй, "
                        "Host ∈ %s) хандахыг зөвшөөрнө; бусад хүсэлт 403. Нэмэлт Host: %s.", mode,
                        ", ".join(sorted(trusted)), TRUSTED_HOSTS_ENV)
            # L4: Host-ын шалгалт DNS rebinding-ээс л хамгаална — түүхий TCP forwarder-ийн клиент Host: localhost илгээнэ
            log.warning("!!! Port forwarding (ssh -R/-L, socat, ngrok tcp, kubectl port-forward г.м.) энэ портыг өөр "
                        "машинаас хүрэх боломжтой болговол Host шалгалт ХАМГААЛАХГҮЙ — ийм үед заавал T2S_WEB_KEY "
                        "тохируулна уу ('%s' горим).", mode)

    def check_key(k: str | None = None) -> None:
        if web_key:
            if not _eq(k or header_key(), web_key):
                raise HTTPException(status_code=403, detail="Холбоосны түлхүүр буруу (URL-ийн ?k=... хэсгийг бүтнээр нь "
                                                            "нээнэ үү)")
            return
        if is_dev(mode) or insecure_open() or is_local_direct(trusted):
            return
        c = current()
        if c.get("client") in LOOPBACK and not c.get("proxied"):   # localhost, гэхдээ Host итгэмжлэгдээгүй (S2)
            _warn_untrusted_host(c.get("host"), mode)
            raise HTTPException(status_code=403, detail="Сервер түлхүүргүй (T2S_WEB_KEY) тул зөвхөн энэ машины хөтчөөс "
                                                        "http://localhost эсвэл http://127.0.0.1 хаягаар хандана.")
        raise HTTPException(status_code=403, detail="Сервер түлхүүргүй (T2S_WEB_KEY) тул зөвхөн localhost-оос хандана.")
    return check_key


def admin_ok(admin: str | None = None) -> bool:
    """T2S_ADMIN_KEY (тусдаа, нийтлэг түлхүүр биш); тохируулаагүй бол хаалттай."""
    return _eq(admin or header_admin(), os.environ.get("T2S_ADMIN_KEY"))


def require_admin(admin: str | None = None) -> None:
    from fastapi import HTTPException
    if not admin_ok(admin):
        raise HTTPException(status_code=403, detail="Админ түлхүүр (T2S_ADMIN_KEY) шаардлагатай.")


def safe_bind_host(host: str, mode: str | None, web_key: str | None) -> str:
    """Түлхүүргүй, dev биш горимд гадагш (0.0.0.0) сонсохгүй — 127.0.0.1 болгоно."""
    if web_key or is_dev(mode) or insecure_open() or host in LOOPBACK:
        return host
    log.warning("!!! T2S_WEB_KEY тохируулаагүй ('%s' горим) — %s-ийн оронд 127.0.0.1-д холбогдоно. Гаднаас хандах бол "
                "T2S_WEB_KEY тохируулна уу (эсвэл зөвхөн демод T2S_INSECURE_OPEN=1).", mode, host)
    return "127.0.0.1"


# ---------------- зэрэг ажиллах хязгаар (S1, R4 R1) ----------------

class WorkSlots:
    """Pipeline (LLM) ажиллуулах эсвэл SQL гүйцэтгэх БҮХ endpoint-д нэг хуваалцсан хязгаар: /ask, /ask_job,
    /flow/run_job, /analyst/rerun, /analyst/verified/{id}/run. Дүүрсэн бол 429 (Retry-After).
    Синхрон endpoint: `with slots.hold(): ...`. Background job: handler-т `acquire_or_429()`, ажлын thread-ийн
    `finally`-д `release()` (thread эхлүүлж чадаагүй бол handler өөрөө release)."""

    BUSY = "Сервер завгүй байна (зэрэг ажиллах асуулт/SQL-ийн хязгаар) — түр хүлээгээд дахин оролдоно уу."

    def __init__(self, limit: int = 4):
        self.limit = max(1, int(limit))
        self._sem = threading.BoundedSemaphore(self.limit)
        self._lock = threading.Lock()
        self._busy = 0

    @property
    def busy(self) -> int:
        return self._busy

    def try_acquire(self) -> bool:
        if not self._sem.acquire(blocking=False):
            return False
        with self._lock:
            self._busy += 1
        return True

    def release(self) -> None:
        self._sem.release()          # BoundedSemaphore: илүү release хийвэл ValueError (алдааг нуухгүй)
        with self._lock:
            self._busy -= 1

    def acquire_or_429(self, detail: str | None = None) -> None:
        if not self.try_acquire():
            from fastapi import HTTPException
            raise HTTPException(status_code=429, detail=detail or self.BUSY, headers={"Retry-After": "5"})

    @contextmanager
    def hold(self, detail: str | None = None):
        self.acquire_or_429(detail)
        try:
            yield
        finally:
            self.release()


# ---------------- хурдны хязгаар ба хүсэлтийн хэмжээ (3-р аудит H1, L6) ----------------

class RateLimit:
    """Хямд endpoint-ийн (/flow/compile, /feedback) нийт хурдны хязгаар — token bucket (процесс даяар; клиентээр
    ялгахгүй: tunnel/proxy-ийн ард бүх хүсэлт 127.0.0.1-ээс ирнэ). Хэтэрвэл 429 (Retry-After)."""

    BUSY = "Хэт олон хүсэлт — түр хүлээгээд дахин оролдоно уу."

    def __init__(self, rate_per_s: float = 10.0, burst: int = 20, clock=None):
        import time
        self.rate, self.burst = max(0.001, float(rate_per_s)), max(1, int(burst))
        self._clock = clock or time.monotonic
        self._tokens, self._t = float(self.burst), self._clock()
        self._lock = threading.Lock()

    def try_acquire(self) -> bool:
        with self._lock:
            now = self._clock()
            self._tokens = min(float(self.burst), self._tokens + max(0.0, now - self._t) * self.rate)
            self._t = now
            if self._tokens >= 1.0:
                self._tokens -= 1.0
                return True
            return False

    def check_or_429(self, detail: str | None = None) -> None:
        if not self.try_acquire():
            from fastapi import HTTPException
            raise HTTPException(status_code=429, detail=detail or self.BUSY, headers={"Retry-After": "1"})


DEFAULT_MAX_BODY_BYTES = 262_144   # flow (≤ 50 node), SQL (≤ 8000), асуулт (≤ 1000) — хэдэн арван KB-аас хэтрэхгүй
DEFAULT_MAX_RESULT_BYTES = 5_000_000   # H2: вэбийн Executor-ийн нэг үр дүнгийн санах ойн төсөв (db/executor.py)


def _svc_int(cfg, key: str, default: int) -> int | None:
    svc = ((cfg or {}).get("service") or {}) if cfg is not None else {}
    v = svc.get(key, default)
    return int(v) if v else None


def max_result_bytes(cfg) -> int | None:
    """H2: `service.max_result_bytes` (байхгүй бол 5 MB; 0/null — унтраах = хуучин client-side cursor)."""
    return _svc_int(cfg, "max_result_bytes", DEFAULT_MAX_RESULT_BYTES)


def max_body_bytes(cfg) -> int:
    """H1: `service.max_body_bytes` (байхгүй бол 256 KB; 0/null — хязгааргүй)."""
    return _svc_int(cfg, "max_body_bytes", DEFAULT_MAX_BODY_BYTES) or 0


class BodySizeLimit:
    """ASGI middleware: хүсэлтийн биеийн дээд хэмжээ (H1). FastAPI нь JSON биеийг бүхэлд нь санах ойд уншдаг тул
    Content-Length хэтэрвэл endpoint-д хүргэлгүй шууд 413; Content-Length-гүй (chunked) үед уншсан байтыг тоолж,
    хэтэрмэгц 413 (HTTPException — FastAPI-ийн биеийг унших алхам дамжуулна). `app.add_middleware(BodySizeLimit, ...)`."""

    DETAIL = "Хүсэлт хэт том (Request body too large)."

    def __init__(self, app, max_bytes: int = DEFAULT_MAX_BODY_BYTES):
        self.app, self.max_bytes = app, int(max_bytes or 0)

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http" or self.max_bytes <= 0:
            await self.app(scope, receive, send)
            return
        for k, v in scope.get("headers") or []:
            if k.lower() == b"content-length":
                try:
                    n = int(v)
                except ValueError:
                    n = self.max_bytes + 1
                if n > self.max_bytes:
                    from starlette.responses import JSONResponse
                    await JSONResponse({"detail": self.DETAIL}, status_code=413)(scope, receive, send)
                    return
        seen = 0

        async def limited():
            nonlocal seen
            msg = await receive()
            if msg.get("type") == "http.request":
                seen += len(msg.get("body") or b"")
                if seen > self.max_bytes:
                    from fastapi import HTTPException
                    raise HTTPException(status_code=413, detail=self.DETAIL)
            return msg

        await self.app(scope, limited, send)
