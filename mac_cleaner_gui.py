#!/usr/bin/env python3
"""Optional local web GUI for Mac Cleaner (standard library only)."""

from __future__ import annotations

import hashlib
import json
import secrets
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from mac_cleaner import Candidate, default_folders, move_to_trash, partition_candidates, scan


HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Mac Cleaner</title>
<style>
:root{color-scheme:light dark;--bg:#f4f7fb;--card:#fff;--text:#172033;--muted:#667085;--line:#d8dee9;--blue:#1769e0;--blue2:#0e54b9;--warn:#b54708;--warnbg:#fffaeb;--green:#067647}
@media(prefers-color-scheme:dark){:root{--bg:#11151d;--card:#1b212c;--text:#f1f5fb;--muted:#aab4c4;--line:#343e4d;--warn:#fdb022;--warnbg:#292313;--green:#47cd89}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:15px -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}.shell{max-width:1180px;margin:0 auto;padding:38px 28px 60px}h1{font-size:34px;letter-spacing:-1px;margin:0 0 6px}.lead{color:var(--muted);margin:0 0 24px}.toolbar,.summary,.panel{background:var(--card);border:1px solid var(--line);border-radius:14px}.toolbar{display:grid;grid-template-columns:1fr auto auto;gap:14px;padding:16px;align-items:end}.field label{display:block;color:var(--muted);font-size:12px;font-weight:650;margin-bottom:7px}.field input,.field select{width:100%;border:1px solid var(--line);border-radius:9px;background:var(--card);color:var(--text);padding:10px 12px;font:inherit}.age{width:130px}.button{border:1px solid var(--line);border-radius:9px;background:var(--card);color:var(--text);padding:10px 15px;font:650 14px inherit;cursor:pointer}.button:hover{filter:brightness(.96)}.button.primary{background:var(--blue);border-color:var(--blue);color:#fff}.button.primary:hover{background:var(--blue2)}.button:disabled{opacity:.45;cursor:not-allowed}.summary{display:flex;justify-content:space-between;align-items:center;padding:16px 18px;margin:14px 0}.summary strong{font-size:17px}.status{color:var(--muted)}.grid{display:grid;grid-template-columns:1fr 1fr;gap:14px}.panel{overflow:hidden}.panel-head{padding:17px 18px 13px;border-bottom:1px solid var(--line)}.panel h2{font-size:18px;margin:0 0 3px}.panel-head p{color:var(--muted);font-size:13px;margin:0}.files{height:390px;overflow:auto}.empty{color:var(--muted);padding:44px 18px;text-align:center}.file{display:grid;grid-template-columns:auto 1fr auto;gap:12px;padding:13px 17px;border-bottom:1px solid var(--line);align-items:start}.file:last-child{border-bottom:0}.file-name{font-weight:650;overflow-wrap:anywhere}.meta{color:var(--muted);font-size:12px;margin-top:4px;overflow-wrap:anywhere}.size{font-variant-numeric:tabular-nums;white-space:nowrap;font-size:13px}.review-note{background:var(--warnbg);color:var(--warn);font-size:12px;padding:9px 18px}.panel-actions{border-top:1px solid var(--line);padding:14px 17px;display:flex;justify-content:flex-end}.check{accent-color:var(--blue);width:17px;height:17px;margin-top:2px}.safe{color:var(--green)}.foot{display:flex;justify-content:space-between;color:var(--muted);font-size:12px;margin-top:16px}.toast{position:fixed;right:22px;bottom:22px;background:var(--text);color:var(--bg);padding:13px 17px;border-radius:10px;box-shadow:0 8px 30px #0004;display:none;max-width:420px}@media(max-width:780px){.toolbar{grid-template-columns:1fr}.age{width:100%}.grid{grid-template-columns:1fr}.files{height:300px}}
</style>
</head>
<body><main class="shell">
<h1>Mac Cleaner</h1>
<p class="lead">Recover space safely. Recommended clutter is one click; important files stay protected.</p>
<section class="toolbar">
  <div class="field"><label for="folders">Folders to scan (separate with commas)</label><input id="folders"></div>
  <div class="field age"><label for="age">Minimum age</label><select id="age"><option>1</option><option selected>7</option><option>14</option><option>30</option><option>60</option><option>90</option></select></div>
  <button class="button primary" id="scan">Scan now</button>
</section>
<section class="summary"><strong id="summary">Ready to scan</strong><span class="status" id="status">Local and private</span></section>
<div class="grid">
  <section class="panel"><div class="panel-head"><h2>Recommended cleanup</h2><p>Old, recognizable clutter</p></div><div class="files" id="recommended"><div class="empty">Scan to find clutter</div></div><div class="panel-actions"><button class="button primary" id="cleanRecommended" disabled>Move recommended to Trash</button></div></section>
  <section class="panel"><div class="panel-head"><h2>Needs review</h2><p>Recent or unusually large files</p></div><div class="review-note">These files are never included automatically.</div><div class="files" id="review"><div class="empty">Protected files will appear here</div></div><div class="panel-actions"><button class="button" id="cleanReview" disabled>Move selected review files…</button></div></section>
</div>
<div class="foot"><span>Everything is moved to Trash and can be restored.</span><button class="button" id="quit">Stop GUI server</button></div>
</main><div class="toast" id="toast"></div>
<script>
const TOKEN=__TOKEN__;
const $=id=>document.getElementById(id);
let recommended=[],review=[];
async function api(path,body={}){const response=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json','X-Mac-Cleaner-Token':TOKEN},body:JSON.stringify(body)});const data=await response.json();if(!response.ok)throw new Error(data.error||'Request failed');return data}
function esc(value){const node=document.createElement('span');node.textContent=value;return node.innerHTML}
function row(item,checkbox=false){return `<div class="file">${checkbox?`<input class="check review-check" type="checkbox" value="${item.id}">`:'<span class="safe">✓</span>'}<div><div class="file-name">${esc(item.name)}</div><div class="meta">${esc(item.reason)} · ${item.age_days} days old<br>${esc(item.path)}</div></div><div class="size">${esc(item.size)}</div></div>`}
function render(){ $('recommended').innerHTML=recommended.length?recommended.map(x=>row(x)).join(''):'<div class="empty">No recommended clutter found</div>';$('review').innerHTML=review.length?review.map(x=>row(x,true)).join(''):'<div class="empty">No protected files found</div>';$('cleanRecommended').disabled=!recommended.length;$('cleanReview').disabled=true;document.querySelectorAll('.review-check').forEach(x=>x.addEventListener('change',()=>{$('cleanReview').disabled=!document.querySelectorAll('.review-check:checked').length})) }
function toast(text){$('toast').textContent=text;$('toast').style.display='block';setTimeout(()=>$('toast').style.display='none',4500)}
async function doScan(){try{$('scan').disabled=true;$('status').textContent='Scanning…';const data=await api('/api/scan',{folders:$('folders').value.split(',').map(x=>x.trim()).filter(Boolean),min_age:Number($('age').value)});recommended=data.recommended;review=data.review;$('summary').textContent=`${recommended.length} recommended · ${review.length} need review · ${data.total_size} found`;$('status').textContent=data.warnings.length?`${data.warnings.length} folder warning(s)`:'Scan complete';render();if(data.warnings.length)toast(data.warnings.join('\n'))}catch(e){toast(e.message);$('status').textContent='Scan failed'}finally{$('scan').disabled=false}}
$('scan').onclick=doScan;
$('cleanRecommended').onclick=async()=>{try{$('status').textContent='Moving to Trash…';const data=await api('/api/clean',{ids:recommended.map(x=>x.id),confirm_review:false});toast(`Moved ${data.moved} file(s), ${data.size}, to Trash`);await doScan()}catch(e){toast(e.message)}};
$('cleanReview').onclick=async()=>{const ids=[...document.querySelectorAll('.review-check:checked')].map(x=>x.value);const chosen=review.filter(x=>ids.includes(x.id));if(!confirm(`These ${chosen.length} protected file(s) are recent or very large. Move them to Trash?\n\n${chosen.slice(0,8).map(x=>'• '+x.name).join('\n')}`))return;try{const data=await api('/api/clean',{ids,confirm_review:true});toast(`Moved ${data.moved} file(s), ${data.size}, to Trash`);await doScan()}catch(e){toast(e.message)}};
$('quit').onclick=async()=>{await api('/api/shutdown');document.body.innerHTML='<main class="shell"><h1>Mac Cleaner stopped</h1><p class="lead">You can close this tab.</p></main>'};
fetch('/api/config',{headers:{'X-Mac-Cleaner-Token':TOKEN}}).then(x=>x.json()).then(data=>{$('folders').value=data.folders.join(', ');doScan()});
</script></body></html>"""


def human_size(size: int) -> str:
    units = ("B", "KB", "MB", "GB", "TB")
    value = float(size)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.1f} {unit}" if unit != "B" else f"{size} B"
        value /= 1024
    return f"{size} B"


def item_id(item: Candidate) -> str:
    return hashlib.sha256(str(item.path).encode("utf-8")).hexdigest()[:20]


def serialize(item: Candidate) -> dict[str, Any]:
    return {
        "id": item_id(item), "name": item.path.name, "path": str(item.path),
        "size": human_size(item.size), "age_days": item.age_days,
        "reason": item.reason, "important": item.important,
    }


class CleanerServer(ThreadingHTTPServer):
    def __init__(self, token: str) -> None:
        super().__init__(("127.0.0.1", 0), CleanerHandler)
        self.token = token
        self.items: dict[str, Candidate] = {}
        self.lock = threading.Lock()


class CleanerHandler(BaseHTTPRequestHandler):
    server: CleanerServer

    def log_message(self, _format: str, *args: object) -> None:
        return

    def _authorized(self) -> bool:
        query_token = parse_qs(urlparse(self.path).query).get("token", [""])[0]
        return secrets.compare_digest(self.headers.get("X-Mac-Cleaner-Token", "") or query_token,
                                      self.server.token)

    def _json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length > 1_000_000:
            raise ValueError("Request is too large")
        return json.loads(self.rfile.read(length) or b"{}")

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if not self._authorized():
            self._json(403, {"error": "Unauthorized"})
            return
        if parsed.path == "/":
            body = HTML.replace("__TOKEN__", json.dumps(self.server.token)).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
        elif parsed.path == "/api/config":
            self._json(200, {"folders": [str(path) for path in default_folders()]})
        else:
            self._json(404, {"error": "Not found"})

    def do_POST(self) -> None:
        if not self._authorized():
            self._json(403, {"error": "Unauthorized"})
            return
        try:
            data = self._body()
            path = urlparse(self.path).path
            if path == "/api/scan":
                self._scan(data)
            elif path == "/api/clean":
                self._clean(data)
            elif path == "/api/shutdown":
                self._json(200, {"ok": True})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
            else:
                self._json(404, {"error": "Not found"})
        except (ValueError, TypeError, json.JSONDecodeError) as error:
            self._json(400, {"error": str(error)})
        except Exception as error:
            self._json(500, {"error": f"Operation failed: {error}"})

    def _scan(self, data: dict[str, Any]) -> None:
        folders = [Path(value) for value in data.get("folders", []) if isinstance(value, str)]
        minimum_age = int(data.get("min_age", 7))
        if minimum_age < 0:
            raise ValueError("Minimum age cannot be negative")
        candidates, warnings = scan(folders or default_folders(), minimum_age)
        recommended, review = partition_candidates(candidates)
        with self.server.lock:
            self.server.items = {item_id(item): item for item in candidates}
        self._json(200, {
            "recommended": [serialize(item) for item in recommended],
            "review": [serialize(item) for item in review],
            "total_size": human_size(sum(item.size for item in candidates)),
            "warnings": warnings,
        })

    def _clean(self, data: dict[str, Any]) -> None:
        requested = data.get("ids", [])
        if not isinstance(requested, list):
            raise ValueError("Invalid file selection")
        with self.server.lock:
            selected = [self.server.items[value] for value in requested if value in self.server.items]
        if len(selected) != len(set(requested)):
            raise ValueError("Some selected files are no longer available; scan again")
        if any(item.important for item in selected) and data.get("confirm_review") is not True:
            raise ValueError("Protected files require explicit confirmation")
        moved, bytes_moved, errors = move_to_trash(selected, Path.home() / ".Trash")
        if errors:
            raise ValueError("\n".join(errors))
        self._json(200, {"moved": moved, "size": human_size(bytes_moved)})


def main() -> None:
    token = secrets.token_urlsafe(24)
    server = CleanerServer(token)
    url = f"http://127.0.0.1:{server.server_port}/?token={token}"
    print(f"Mac Cleaner GUI: {url}")
    print("The interface is local to this Mac. Press Ctrl+C to stop it.")
    threading.Timer(0.25, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
