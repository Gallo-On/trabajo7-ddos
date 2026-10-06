#!/usr/bin/env python3
"""Bounded HTTP load tester for explicitly authorized private lab hosts."""

from __future__ import annotations

import argparse
import ipaddress
import ssl
import socket
import threading
import time
import urllib.error
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

MAX_DURATION_SECONDS = 120
MAX_CONCURRENCY = 100
MAX_REQUESTS_PER_SECOND = 200
REQUEST_TIMEOUT_SECONDS = 3
REPORT_INTERVAL_SECONDS = 2


def bounded_integer(maximum: int):
    def parse(raw_value: str) -> int:
        try:
            value = int(raw_value)
        except ValueError as error:
            raise argparse.ArgumentTypeError("must be an integer") from error
        if not 1 <= value <= maximum:
            raise argparse.ArgumentTypeError(f"must be between 1 and {maximum}")
        return value

    return parse


def resolve_private_host(host: str) -> list[str]:
    if not host or any(character.isspace() for character in host) or "/" in host:
        raise argparse.ArgumentTypeError("host must be a hostname or IP address, without a URL or path")

    try:
        addresses = {str(ipaddress.ip_address(host.strip("[]")))}
    except ValueError:
        try:
            addresses = {
                result[4][0]
                for result in socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
            }
        except socket.gaierror as error:
            raise argparse.ArgumentTypeError(f"cannot resolve host {host!r}: {error}") from error

    if not addresses:
        raise argparse.ArgumentTypeError("host did not resolve to any address")

    for address in addresses:
        parsed_address = ipaddress.ip_address(address)
        if (
            parsed_address.is_unspecified
            or parsed_address.is_multicast
            or parsed_address.is_link_local
            or parsed_address.is_reserved
            or not (parsed_address.is_private or parsed_address.is_loopback)
        ):
            raise argparse.ArgumentTypeError(
                f"host resolves to disallowed address {address}; only private lab targets are accepted"
            )
    return sorted(addresses)


def request_status(opener: urllib.request.OpenerDirector, url: str) -> str:
    request = urllib.request.Request(url, method="GET")
    try:
        with opener.open(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            return str(response.status)
    except urllib.error.HTTPError as error:
        return str(error.code)
    except (urllib.error.URLError, TimeoutError, OSError):
        return "000"


def ldaps_status(host: str, port: int, bind_dn: str, password: str) -> str:
    try:
        from ldap3 import Connection, NONE, Server, Tls
    except ImportError as error:
        raise RuntimeError("LDAPS mode requires dependencies from requirements.txt") from error

    server = Server(
        host,
        port=port,
        use_ssl=True,
        tls=Tls(validate=ssl.CERT_NONE),
        get_info=NONE,
        connect_timeout=REQUEST_TIMEOUT_SECONDS,
    )
    connection = Connection(server, user=bind_dn, password=password, auto_bind=False)
    try:
        if connection.bind():
            return "LDAP bind=success"
        return f"LDAP err={connection.result.get('result', 'unknown')}"
    except Exception:
        return "000"
    finally:
        connection.unbind()


class RateLimiter:
    def __init__(self, requests_per_second: int) -> None:
        self.interval = 1 / requests_per_second
        self.next_request_at = 0.0
        self.lock = threading.Lock()

    def wait_for_turn(self, stop_event: threading.Event) -> bool:
        with self.lock:
            now = time.monotonic()
            scheduled_at = max(now, self.next_request_at)
            self.next_request_at = scheduled_at + self.interval
        return not stop_event.wait(max(0.0, scheduled_at - now))


def run_test(
    protocol: str,
    host: str,
    port: int,
    path: str,
    duration: int,
    concurrency: int,
    rps: int,
    bind_dn: str,
    password: str,
) -> None:
    addresses = resolve_private_host(host)
    if protocol == "http" and (not path.startswith("/") or any(character in path for character in "\r\n ")):
        raise argparse.ArgumentTypeError("path must start with '/' and contain no whitespace")

    if protocol == "http":
        url_host = f"[{host.strip('[]')}]" if ":" in host else host
        target = f"http://{url_host}:{port}{path}"
    else:
        target = f"ldaps://{host}:{port} (bind DN: {bind_dn})"

    print(f"Target: {target} (resolved: {', '.join(addresses)})")
    print(
        f"Bounded run: {duration}s, {concurrency} workers, at most {rps} operations/s; "
        "Ctrl-C stops the run."
    )

    counts: Counter[str] = Counter()
    counts_lock = threading.Lock()
    stop_event = threading.Event()
    rate_limiter = RateLimiter(rps)
    start_time = time.monotonic()
    deadline = start_time + duration

    def worker() -> None:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({})) if protocol == "http" else None
        while not stop_event.is_set() and time.monotonic() < deadline:
            if not rate_limiter.wait_for_turn(stop_event):
                break
            if time.monotonic() >= deadline:
                break
            if protocol == "http":
                status = request_status(opener, target)
            else:
                status = ldaps_status(host, port, bind_dn, password)
            with counts_lock:
                counts[status] += 1

    try:
        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            futures = [executor.submit(worker) for _ in range(concurrency)]
            while time.monotonic() < deadline:
                time.sleep(min(REPORT_INTERVAL_SECONDS, max(0, deadline - time.monotonic())))
                with counts_lock:
                    snapshot = counts.copy()
                total = sum(snapshot.values())
                print(f"[{datetime.now():%H:%M:%S}] total={total} statuses={dict(snapshot)}", flush=True)
            stop_event.set()
            for future in futures:
                future.result()
    except KeyboardInterrupt:
        stop_event.set()
        print("\nStopping workers after Ctrl-C...", flush=True)

    elapsed = max(time.monotonic() - start_time, 0.001)
    with counts_lock:
        final_counts = counts.copy()
    total = sum(final_counts.values())
    print("\nRESULT")
    print(f"elapsed={elapsed:.1f}s total={total} requests_per_second={total / elapsed:.1f}")
    if protocol == "http":
        print(
            f"http_200={final_counts['200']} non_200_or_connection_errors={total - final_counts['200']} "
            f"status_counts={dict(final_counts)}"
        )
    else:
        print(
            f"ldap_bind_rejected={sum(count for status, count in final_counts.items() if status.startswith('LDAP err='))} "
            f"transport_errors={final_counts['000']} status_counts={dict(final_counts)}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--protocol", choices=("http", "ldaps"), default="http")
    parser.add_argument("--host", required=True, help="private/loopback IP or resolvable private hostname")
    parser.add_argument("--port", required=True, type=bounded_integer(65535), help="service port (1-65535)")
    parser.add_argument("--path", default="/", help="HTTP path; default: /")
    parser.add_argument(
        "--bind-dn",
        default="uid=fail2ban-test,ou=users,dc=example,dc=com",
        help="LDAPS test identity; use a dedicated lab identity",
    )
    parser.add_argument("--password", default="invalid-fail2ban-test-password", help="LDAPS test password")
    parser.add_argument("--duration", type=bounded_integer(MAX_DURATION_SECONDS), default=15)
    parser.add_argument("--concurrency", type=bounded_integer(MAX_CONCURRENCY), default=5)
    parser.add_argument("--rps", type=bounded_integer(MAX_REQUESTS_PER_SECOND), default=10)
    args = parser.parse_args()
    try:
        run_test(
            args.protocol,
            args.host,
            args.port,
            args.path,
            args.duration,
            args.concurrency,
            args.rps,
            args.bind_dn,
            args.password,
        )
    except (argparse.ArgumentTypeError, RuntimeError) as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()