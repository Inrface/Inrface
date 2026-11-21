"""Outbound requests to an address a person typed in.

The server must not be usable to reach things only it can see (its own network, cloud metadata),
so the name is looked up first, every address it points to must be public, and the request then
goes to that exact address. Redirects are not followed.
"""
import ipaddress
import socket
import time

import httpx

MAX_REPLY = 64 * 1024
MAX_SECONDS = 20.0  # for the whole request, however slowly the other end answers

# Ranges Python's is_global lets through that can still lead inside: addresses that a NAT64 gateway
# turns back into IPv4 ones, the old site-local range, and multicast.
_NOT_PUBLIC = tuple(ipaddress.ip_network(n) for n in ("64:ff9b::/96", "64:ff9b:1::/48", "fec0::/10", "ff00::/8", "224.0.0.0/4"))


def is_public(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address.split("%")[0])
    except ValueError:
        return False
    if getattr(ip, "ipv4_mapped", None):
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast and not any(ip in n for n in _NOT_PUBLIC if n.version == ip.version)


class UnsafeAddress(Exception):
    """The message is safe to show."""


def check_url(url: str, allow_private: bool = False) -> httpx.URL:
    try:
        u = httpx.URL(url)
    except httpx.InvalidURL:
        raise UnsafeAddress("That isn't a valid address.")
    if u.scheme != "https" and not (allow_private and u.scheme == "http"):
        raise UnsafeAddress("The address must start with https://")
    if not u.host or u.userinfo:
        raise UnsafeAddress("That isn't a valid address.")
    return u


def _public_address(host: str, port: int, allow_private: bool) -> str:
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError):
        raise UnsafeAddress("That address couldn't be found.")
    addresses = list(dict.fromkeys(i[4][0] for i in infos))
    if not addresses:
        raise UnsafeAddress("That address couldn't be found.")
    if not allow_private:
        for a in addresses:
            if not is_public(a):
                raise UnsafeAddress("That address points inside a private network. Set WEBHOOK_ALLOW_PRIVATE=1 on the server to allow it.")
    return addresses[0]


def post(url: str, body: bytes, headers: dict, allow_private: bool = False, timeout: float = 10.0) -> tuple[int, bytes]:
    """POST and return (status, the first part of the reply). Raises UnsafeAddress or httpx.HTTPError."""
    u = check_url(url, allow_private)
    port = u.port or (443 if u.scheme == "https" else 80)
    address = _public_address(u.host, port, allow_private)
    target = u.copy_with(host=address)
    deadline = time.monotonic() + MAX_SECONDS
    with httpx.Client(timeout=timeout, follow_redirects=False) as c:
        with c.stream("POST", target, content=body, headers={**headers, "Host": u.netloc.decode("ascii")},
                      extensions={"sni_hostname": u.host}) as r:
            reply = b""
            for chunk in r.iter_bytes():
                reply += chunk
                if len(reply) >= MAX_REPLY or time.monotonic() > deadline:
                    break
            return r.status_code, reply[:MAX_REPLY]
