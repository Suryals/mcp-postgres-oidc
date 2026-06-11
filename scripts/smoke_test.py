#!/usr/bin/env python3
"""End-to-end smoke test: run the same query as different roles and show masking.

Exercises the full path: Keycloak token -> Traefik -> OIDC middleware -> FastMCP.
Usage: uv run --with httpx scripts/smoke_test.py
"""
import json
import sys
import httpx

KC = "http://keycloak.test/realms/mcp-db/protocol/openid-connect/token"
MCP = "http://mcp-postgres.traefik.test/mcp"


def get_token(user: str, pw: str) -> str:
    r = httpx.post(KC, data={
        "grant_type": "password", "client_id": "mcp-test",
        "client_secret": "mcp-test-secret", "username": user, "password": pw,
    })
    r.raise_for_status()
    return r.json()["access_token"]


def _parse_sse(text: str):
    for line in text.splitlines():
        if line.startswith("data: "):
            return json.loads(line[6:])
    return None


def call_tool(token: str, name: str, args: dict):
    """Full streamable-HTTP handshake, then one tools/call."""
    h = {"Authorization": f"Bearer {token}",
         "Content-Type": "application/json",
         "Accept": "application/json, text/event-stream"}
    with httpx.Client(timeout=30) as c:
        init = c.post(MCP, headers=h, json={
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                       "clientInfo": {"name": "smoke", "version": "1.0"}}})
        sid = init.headers["Mcp-Session-Id"]
        h["Mcp-Session-Id"] = sid
        c.post(MCP, headers=h, json={"jsonrpc": "2.0",
               "method": "notifications/initialized"})
        resp = c.post(MCP, headers=h, json={
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": name, "arguments": args}})
        return _parse_sse(resp.text)


def show(label: str, token: str):
    print(f"\n{'='*70}\n{label}\n{'='*70}")
    res = call_tool(token, "query", {
        "sql": "SELECT first_name, ssn, email, date_of_birth "
               "FROM customers ORDER BY id LIMIT 3"})
    if res and "result" in res:
        for block in res["result"].get("content", []):
            print(block.get("text", block))
    else:
        print("ERROR:", json.dumps(res, indent=2))


if __name__ == "__main__":
    users = [("alice", "alice123", "ADMIN"),
             ("bob", "bob123", "ANALYST"),
             ("carol", "carol123", "READONLY")]
    for u, p, role in users:
        show(f"{u} ({role})", get_token(u, p))
