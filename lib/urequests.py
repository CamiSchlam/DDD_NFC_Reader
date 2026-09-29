# Minimal MicroPython HTTP client (subset of micropython-lib's urequests),
# vendored here since it isn't built into the firmware.
import usocket


class Response:
    def __init__(self, f):
        self.raw = f
        self.encoding = "utf-8"
        self._cached = None

    def close(self):
        if self.raw:
            self.raw.close()
            self.raw = None
        self._cached = None

    @property
    def content(self):
        if self._cached is None:
            try:
                self._cached = self.raw.read()
            finally:
                self.raw.close()
                self.raw = None
        return self._cached

    @property
    def text(self):
        return str(self.content, self.encoding)

    def json(self):
        import ujson
        return ujson.loads(self.content)


def request(method, url, data=None, json=None, headers=None, timeout_s=10):
    if headers is None:
        headers = {}

    try:
        proto, _, host, path = url.split("/", 3)
    except ValueError:
        proto, _, host = url.split("/", 2)
        path = ""

    if proto == "http:":
        port = 80
    elif proto == "https:":
        try:
            import ussl as _ssl_mod
        except ImportError:
            import ssl as _ssl_mod
        port = 443
    else:
        raise ValueError("Unsupported protocol: " + proto)

    if ":" in host:
        host, port = host.split(":", 1)
        port = int(port)

    ai = usocket.getaddrinfo(host, port, 0, usocket.SOCK_STREAM)[0]
    s = usocket.socket(ai[0], ai[1], ai[2])
    s.settimeout(timeout_s)
    try:
        s.connect(ai[-1])
        if proto == "https:":
            s = _ssl_mod.wrap_socket(s, server_hostname=host)

        s.write(b"%s /%s HTTP/1.0\r\n" % (method, path))
        if "Host" not in headers:
            s.write(b"Host: %s\r\n" % host)

        if json is not None:
            import ujson
            data = ujson.dumps(json)
            headers["Content-Type"] = "application/json"

        for k in headers:
            s.write(k)
            s.write(b": ")
            s.write(headers[k])
            s.write(b"\r\n")

        if data:
            s.write(b"Content-Length: %d\r\n" % len(data))
        s.write(b"\r\n")
        if data:
            s.write(data)

        line = s.readline()
        _, status, reason = line.split(None, 2)
        status = int(status)
        while True:
            line = s.readline()
            if not line or line == b"\r\n":
                break
    except OSError:
        s.close()
        raise

    resp = Response(s)
    resp.status_code = status
    resp.reason = reason.rstrip()
    return resp


def get(url, **kw):
    return request("GET", url, **kw)


def post(url, **kw):
    return request("POST", url, **kw)
