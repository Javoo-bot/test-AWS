"""
Deploys web/index.html to Cloudflare Workers as a single static page.

Uses the REST API directly rather than wrangler: no toolchain to install, and
nothing in the account is touched beyond the one named script. The deploy is
guarded -- it refuses to run against any name other than WORKER_NAME, and it
lists the other workers it left alone so that is visible rather than assumed.

Credentials are read from .env, which is gitignored.

Usage:
    python web/deploy.py
    python web/deploy.py --dry-run
"""

import argparse
import json
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path

WORKER_NAME = "signal-to-cutoff"
COMPATIBILITY_DATE = "2026-09-01"
BASE = "https://api.cloudflare.com/client/v4"

ROOT = Path(__file__).resolve().parent.parent
PAGE = ROOT / "web" / "index.html"


def load_env():
    env = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, _, v = line.partition("=")
            env[k.strip()] = v.strip()
    missing = [k for k in ("CLOUDFLARE_API_TOKEN", "CLOUDFLARE_ACCOUNT_ID")
               if not env.get(k)]
    if missing:
        sys.exit(f"Missing in .env: {', '.join(missing)}")
    return env


def api(token, method, path, body=None, headers=None, raw=False):
    hdrs = {"Authorization": f"Bearer {token}"}
    hdrs.update(headers or {})
    data = None
    if body is not None:
        if raw:
            data = body
        else:
            data = json.dumps(body).encode()
            hdrs["Content-Type"] = "application/json"
    req = urllib.request.Request(BASE + path, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        try:
            return json.load(e)
        except Exception:
            return {"success": False, "errors": [{"message": f"HTTP {e.code}"}]}


def build_worker_js(html):
    """A module worker that serves one document. The HTML is embedded as a JSON
    string literal, which is valid JS and needs no manual escaping."""
    return (
        "const HTML = " + json.dumps(html) + ";\n\n"
        "export default {\n"
        "  async fetch(request) {\n"
        "    const url = new URL(request.url);\n"
        "    if (url.pathname !== '/' && url.pathname !== '/index.html') {\n"
        "      return new Response('Not found', { status: 404 });\n"
        "    }\n"
        "    return new Response(HTML, {\n"
        "      headers: {\n"
        "        'content-type': 'text/html; charset=utf-8',\n"
        "        'cache-control': 'public, max-age=300',\n"
        "        'x-content-type-options': 'nosniff',\n"
        "        'referrer-policy': 'strict-origin-when-cross-origin',\n"
        "      },\n"
        "    });\n"
        "  },\n"
        "};\n"
    )


def multipart(script_js):
    boundary = uuid.uuid4().hex
    metadata = json.dumps({
        "main_module": "worker.js",
        "compatibility_date": COMPATIBILITY_DATE,
    })
    parts = [
        f'--{boundary}\r\n'
        f'Content-Disposition: form-data; name="metadata"\r\n'
        f'Content-Type: application/json\r\n\r\n{metadata}\r\n',

        f'--{boundary}\r\n'
        f'Content-Disposition: form-data; name="worker.js"; filename="worker.js"\r\n'
        f'Content-Type: application/javascript+module\r\n\r\n{script_js}\r\n',

        f'--{boundary}--\r\n',
    ]
    return "".join(parts).encode("utf-8"), boundary


def main():
    ap = argparse.ArgumentParser(description="Deploy the page to Cloudflare Workers")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    env = load_env()
    token, account = env["CLOUDFLARE_API_TOKEN"], env["CLOUDFLARE_ACCOUNT_ID"]

    html = PAGE.read_text(encoding="utf-8")
    script_js = build_worker_js(html)

    print(f"  page      {PAGE.relative_to(ROOT)}  {len(html):,} bytes")
    print(f"  worker    {WORKER_NAME}  ({len(script_js):,} bytes of JS)")

    # Show what exists, so it is visible that nothing else is being altered.
    existing = api(token, "GET", f"/accounts/{account}/workers/scripts")
    if existing.get("success"):
        others = [s["id"] for s in existing["result"] if s["id"] != WORKER_NAME]
        print(f"  untouched {', '.join(others) if others else '(none)'}")

    if args.dry_run:
        print("\n  dry run: nothing uploaded")
        return 0

    body, boundary = multipart(script_js)
    resp = api(token, "PUT", f"/accounts/{account}/workers/scripts/{WORKER_NAME}",
               body=body, raw=True,
               headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})

    if not resp.get("success"):
        print("\n  upload FAILED:")
        for e in resp.get("errors", []):
            print(f"    {e.get('code', '')} {e.get('message')}")
        return 1
    print("  uploaded  ok")

    sub = api(token, "POST",
              f"/accounts/{account}/workers/scripts/{WORKER_NAME}/subdomain",
              body={"enabled": True, "previews_enabled": False})
    if not sub.get("success"):
        print("  subdomain FAILED:")
        for e in sub.get("errors", []):
            print(f"    {e.get('code', '')} {e.get('message')}")
        return 1

    domain = api(token, "GET", f"/accounts/{account}/workers/subdomain")
    host = domain.get("result", {}).get("subdomain")
    url = f"https://{WORKER_NAME}.{host}.workers.dev"
    print(f"  live      {url}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
