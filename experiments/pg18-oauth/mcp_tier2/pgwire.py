"""
Minimal synchronous PostgreSQL client that authenticates with a bearer token via
SASL OAUTHBEARER (token-first) and runs a single SELECT, returning rows as dicts.

This exists because asyncpg/psycopg3 don't yet speak OAUTHBEARER. It's the piece
that lets the MCP server open a per-user connection to Postgres 18 — no shared
service account. Identity = the token; the login role names the privilege wanted.
"""
import socket
import struct


def _recvn(s, n):
    b = b""
    while len(b) < n:
        c = s.recv(n - len(b))
        if not c:
            raise EOFError("connection closed")
        b += c
    return b


def _recv_msg(s):
    t = s.recv(1)
    if not t:
        return None, None
    (ln,) = struct.unpack("!I", _recvn(s, 4))
    return t, _recvn(s, ln - 4)


def _parse_fields(data):
    f, i = {}, 0
    while i < len(data) and data[i] != 0:
        j = data.index(0, i + 1)
        f[chr(data[i])] = data[i + 1:j].decode("utf-8", "replace")
        i = j + 1
    return f


class PgDenied(Exception):
    """Postgres or the IdP refused the connection or the query."""


def run_select(host, port, login_role, token, sql, timeout=15):
    """
    Connect to Postgres as `login_role`, authenticating with `token` over
    OAUTHBEARER, run `sql`, and return (columns, rows).
    Raises PgDenied if the IdP rejects the connection or Postgres rejects the query.
    """
    s = socket.create_connection((host, port), timeout=timeout)
    try:
        # StartupMessage: protocol 3.0, user = the role we want to assume
        params = b"user\x00" + login_role.encode() + b"\x00database\x00postgres\x00\x00"
        s.sendall(struct.pack("!I", len(params) + 8) + struct.pack("!I", 196608) + params)

        t, data = _recv_msg(s)
        if t == b"R" and struct.unpack("!I", data[:4])[0] == 10:  # AuthenticationSASL
            gs2 = ("n,,\x01auth=Bearer " + token + "\x01\x01").encode()
            payload = b"OAUTHBEARER\x00" + struct.pack("!I", len(gs2)) + gs2
            s.sendall(b"p" + struct.pack("!I", len(payload) + 4) + payload)

        authed = False
        while True:
            t, data = _recv_msg(s)
            if t is None:
                raise PgDenied("connection closed before authentication")
            if t == b"E":
                raise PgDenied(_parse_fields(data).get("M", "auth error"))
            if t == b"R":
                at = struct.unpack("!I", data[:4])[0]
                if at == 0:
                    authed = True
                elif at == 11:  # SASLContinue => token rejected
                    s.sendall(b"p" + struct.pack("!I", 5) + b"\x01")
            if t == b"Z":
                break
        if not authed:
            raise PgDenied("not authenticated")

        # Simple Query
        s.sendall(b"Q" + struct.pack("!I", len(sql) + 5) + sql.encode() + b"\x00")
        columns, rows = [], []
        while True:
            t, data = _recv_msg(s)
            if t == b"E":
                raise PgDenied(_parse_fields(data).get("M", "query error"))
            elif t == b"T":  # RowDescription
                (nf,) = struct.unpack("!H", data[:2]); off = 2; columns = []
                for _ in range(nf):
                    end = data.index(0, off)
                    columns.append(data[off:end].decode())
                    off = end + 1 + 18  # skip cstring NUL + 18 bytes of field meta
            elif t == b"D":  # DataRow
                (nf,) = struct.unpack("!H", data[:2]); off = 2; vals = []
                for _ in range(nf):
                    (ln,) = struct.unpack("!i", data[off:off + 4]); off += 4
                    if ln == -1:
                        vals.append(None)
                    else:
                        vals.append(data[off:off + ln].decode("utf-8", "replace")); off += ln
                rows.append(dict(zip(columns, vals)))
            elif t == b"Z":
                break
        return columns, rows
    finally:
        try:
            s.sendall(b"X" + struct.pack("!I", 4))  # Terminate
        except OSError:
            pass
        s.close()
