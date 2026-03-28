import ssl

from yarl import URL

DOMAIN = "penplotter"
CONF_VERIFY_SSL = "verify_ssl"
DEFAULT_PORT = 4443
SCAN_INTERVAL_SECONDS = 15


def build_base_url(host: str, port: int) -> str:
    return str(URL.build(scheme="https", host=host, port=port))


def make_ssl_context(verify: bool = True) -> ssl.SSLContext | bool:
    if verify:
        return True
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx
