"""
Drive the Tier-2 MCP server over the MCP protocol as a real user — proving the
chain end to end: user identity → MCP → token-exchange → Postgres runs AS the user.

Usage: drive.py <user> <password> <tool> ['<sql>']
"""
import json
import sys
import httpx

KC = "https://keycloak.pg.test:8443/realms/pgoauth/protocol/openid-connect/token"
MCP = "http://127.0.0.1:8009/mcp"


def user_token(user, pw):
    # The session user's login (here via direct grant to the MCP server's client).
    r = httpx.post(KC, verify=False, data={
        "grant_type": "password", "client_id": "mcp-exchanger",
        "client_secret": "mcp-exchanger-secret",
        "username": user, "password": pw, "scope": "openid",
    })
    r.raise_for_status()
    return r.json()["access_token"]


def _sse(text):
    for line in text.splitlines():
        if line.startswith("data: "):
            return json.loads(line[6:])


def call(token, tool, args):
    h = {"Authorization": f"Bearer {token}", "Content-Type": "application/json",
         "Accept": "application/json, text/event-stream"}
    with httpx.Client() as c:
        i = c.post(MCP, headers=h, json={"jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                       "clientInfo": {"name": "drive", "version": "1"}}})
        h["Mcp-Session-Id"] = i.headers["mcp-session-id"]
        c.post(MCP, headers=h, json={"jsonrpc": "2.0", "method": "notifications/initialized"})
        r = c.post(MCP, headers=h, json={"jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": tool, "arguments": args}})
        d = _sse(r.text)
        return json.loads(d["result"]["content"][0]["text"]) if "result" in d else d


if __name__ == "__main__":
    user, pw, tool = sys.argv[1], sys.argv[2], sys.argv[3]
    args = {"sql": sys.argv[4]} if len(sys.argv) > 4 else {}
    print(json.dumps(call(user_token(user, pw), tool, args), indent=2))
