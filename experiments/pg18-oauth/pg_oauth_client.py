#!/usr/bin/env python3
"""
Minimal Postgres-18 client that authenticates with a Keycloak bearer token via
SASL OAUTHBEARER (token-first) — the exact mechanism the MCP server would use once
a Python driver supports it. Proves: identity → Postgres, IdP authorizes the
connection, Postgres enforces column access.

Usage: pg_oauth_client.py <token-user> <password> <login-role> "<SQL>"
"""
import socket, struct, sys, httpx

KC = "https://keycloak.pg.test:8443"
PG = ("localhost", 55432)


def get_token(user, pw):
    r = httpx.post(f"{KC}/realms/pgoauth/protocol/openid-connect/token", verify=False,
                   data={"grant_type": "password", "client_id": "pg-client",
                         "username": user, "password": pw, "scope": "openid"})
    return r.json()["access_token"]


def recvn(s, n):
    b = b""
    while len(b) < n:
        c = s.recv(n - len(b))
        if not c:
            raise EOFError()
        b += c
    return b


def recv_msg(s):
    t = s.recv(1)
    if not t:
        return None, None
    (ln,) = struct.unpack("!I", recvn(s, 4))
    return t, recvn(s, ln - 4)


def parse_err(data):
    f, i = {}, 0
    while i < len(data) and data[i] != 0:
        j = data.index(0, i + 1)
        f[chr(data[i])] = data[i + 1:j].decode()
        i = j + 1
    return f.get("M", str(f))


def run(login_role, token, sql):
    s = socket.create_connection(PG)
    params = b"user\x00" + login_role.encode() + b"\x00database\x00postgres\x00\x00"
    s.sendall(struct.pack("!I", len(params) + 8) + struct.pack("!I", 196608) + params)

    t, data = recv_msg(s)
    if t == b"R" and struct.unpack("!I", data[:4])[0] == 10:               # AuthenticationSASL
        gs2 = ("n,,\x01auth=Bearer " + token + "\x01\x01").encode()
        payload = b"OAUTHBEARER\x00" + struct.pack("!I", len(gs2)) + gs2
        s.sendall(b"p" + struct.pack("!I", len(payload) + 4) + payload)    # SASLInitialResponse

    authed = False
    while True:
        t, data = recv_msg(s)
        if t is None:
            print("  ✗ connection closed before auth"); return
        if t == b"E":
            print("  ⛔ DENIED at connection:", parse_err(data)); return
        if t == b"R":
            at = struct.unpack("!I", data[:4])[0]
            if at == 0:
                authed = True
            elif at == 11:                                                # SASLContinue = failure
                s.sendall(b"p" + struct.pack("!I", 5) + b"\x01")
        if t == b"Z":
            break
    if not authed:
        print("  ✗ not authenticated"); return
    print(f"  ✓ Postgres authenticated the connection as role '{login_role}'")

    s.sendall(b"Q" + struct.pack("!I", len(sql) + 5) + sql.encode() + b"\x00")
    rows = []
    while True:
        t, data = recv_msg(s)
        if t == b"E":
            print("  ⛔ Postgres DENIED the query:", parse_err(data))
        elif t == b"D":
            (nf,) = struct.unpack("!H", data[:2]); off = 2; vals = []
            for _ in range(nf):
                (ln,) = struct.unpack("!i", data[off:off + 4]); off += 4
                if ln == -1:
                    vals.append(None)
                else:
                    vals.append(data[off:off + ln].decode()); off += ln
            rows.append(vals)
        elif t == b"Z":
            break
    if rows:
        print("  ✓ rows:", rows)


if __name__ == "__main__":
    user, pw, role, sql = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
    run(role, get_token(user, pw), sql)
