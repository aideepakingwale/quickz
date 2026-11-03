"""Running behind Amazon CloudFront (the only public address of the site).

CloudFront talks to the server over plain HTTP on the AWS network, so two things need care:

* the server must still know the visitor used https (for join links and the Secure cookie flag): CloudFront says so in the
  `CloudFront-Forwarded-Proto` header;
* nobody should be able to skip CloudFront and talk to the server's address directly: CloudFront adds a secret header
  (`X-Origin-Verify`) to every request it forwards, and anything without it is refused.

Pure ASGI, so it covers WebSockets (the live game) as well as ordinary requests.
"""

import hmac

HEALTH_PATH = "/healthz"          # Docker's own health check has no secret to send


class BehindCloudFront:
    def __init__(self, app, secret: str = "", trust_proto: bool = True):
        self.app = app
        self.secret = secret.encode()
        self.trust_proto = trust_proto

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        headers = dict(scope["headers"])
        if self.secret and scope.get("path") != HEALTH_PATH and not hmac.compare_digest(headers.get(b"x-origin-verify", b""), self.secret):
            await self._refuse(scope, receive, send)
            return
        proto = headers.get(b"cloudfront-forwarded-proto", b"").lower()
        if self.trust_proto and proto in (b"https", b"http"):
            scope = dict(scope)
            secure = proto == b"https"
            scope["scheme"] = ("wss" if secure else "ws") if scope["type"] == "websocket" else ("https" if secure else "http")
        await self.app(scope, receive, send)

    @staticmethod
    async def _refuse(scope, receive, send):
        if scope["type"] == "websocket":
            await receive()                                        # the connect event; refusing before accepting answers HTTP 403
            await send({"type": "websocket.close", "code": 1008})
            return
        body = b'{"error":"This server only answers through its CloudFront address."}'
        await send({"type": "http.response.start", "status": 403, "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})
