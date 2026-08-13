#!/usr/bin/env python3
"""Optional local web GUI for Mac Cleaner (standard library only)."""

from __future__ import annotations

import hashlib
import json
import secrets
import subprocess
import threading
import webbrowser
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from mac_cleaner import (
    Candidate, default_folders, delete_permanently, icloud_drive_folder, load_rules,
    move_to_trash, partition_candidates, read_history, scan, special_storage_candidates,
)


HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Mac Cleaner</title>
<style>
:root{color-scheme:light dark;--bg:#f4f7fb;--card:#fff;--text:#172033;--muted:#667085;--line:#d8dee9;--blue:#1769e0;--blue2:#0e54b9;--warn:#b54708;--warnbg:#fffaeb;--green:#067647}
@media(prefers-color-scheme:dark){:root{--bg:#11151d;--card:#1b212c;--text:#f1f5fb;--muted:#aab4c4;--line:#343e4d;--warn:#fdb022;--warnbg:#292313;--green:#47cd89}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:15px -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}.shell{max-width:1180px;margin:0 auto;padding:38px 28px 60px}h1{font-size:34px;letter-spacing:-1px;margin:0 0 6px}.lead{color:var(--muted);margin:0 0 24px}.toolbar,.summary,.panel,.filters,details{background:var(--card);border:1px solid var(--line);border-radius:14px}.toolbar{display:grid;grid-template-columns:1fr auto auto auto auto;gap:14px;padding:16px;align-items:end}.field label{display:block;color:var(--muted);font-size:12px;font-weight:650;margin-bottom:7px}.field input,.field select{width:100%;border:1px solid var(--line);border-radius:9px;background:var(--card);color:var(--text);padding:10px 12px;font:inherit}.age{width:130px}.button{border:1px solid var(--line);border-radius:9px;background:var(--card);color:var(--text);padding:10px 15px;font:650 14px inherit;cursor:pointer}.button:hover{filter:brightness(.96)}.button.primary{background:var(--blue);border-color:var(--blue);color:#fff}.button.primary:hover{background:var(--blue2)}.button:disabled{opacity:.45;cursor:not-allowed}.summary{display:flex;justify-content:space-between;align-items:center;padding:16px 18px;margin:14px 0}.summary strong{font-size:17px}.status{color:var(--muted)}.filters{display:flex;gap:10px;padding:10px;margin-bottom:14px}.filters input{flex:1}.advanced{padding:12px 16px;margin-top:10px}.advanced label{margin-right:18px}.grid{display:grid;grid-template-columns:1fr 1fr;gap:14px}.panel{overflow:hidden}.panel-head{padding:17px 18px 13px;border-bottom:1px solid var(--line)}.panel h2{font-size:18px;margin:0 0 3px}.panel-head p{color:var(--muted);font-size:13px;margin:0}.files{height:390px;overflow:auto}.empty{color:var(--muted);padding:44px 18px;text-align:center}.file{display:grid;grid-template-columns:auto 1fr auto;gap:12px;padding:13px 17px;border-bottom:1px solid var(--line);align-items:start}.file:last-child{border-bottom:0}.file-name{font-weight:650;overflow-wrap:anywhere}.meta{color:var(--muted);font-size:12px;margin-top:4px;overflow-wrap:anywhere}.size{font-variant-numeric:tabular-nums;white-space:nowrap;font-size:13px}.preview{font-size:11px;padding:4px 7px;margin-top:7px}.review-note{background:var(--warnbg);color:var(--warn);font-size:12px;padding:9px 18px}.panel-actions{border-top:1px solid var(--line);padding:14px 17px;display:flex;justify-content:flex-end}.check{accent-color:var(--blue);width:17px;height:17px;margin-top:2px}.safe{color:var(--green)}.foot{display:flex;justify-content:space-between;color:var(--muted);font-size:12px;margin-top:16px}.history{padding:14px 18px;margin-top:14px}.history summary{cursor:pointer;font-weight:650}.history-row{padding:8px 0;border-bottom:1px solid var(--line);font-size:12px}.toast{position:fixed;right:22px;bottom:22px;background:var(--text);color:var(--bg);padding:13px 17px;border-radius:10px;box-shadow:0 8px 30px #0004;display:none;max-width:420px}@media(max-width:780px){.toolbar{grid-template-columns:1fr}.age{width:100%}.grid{grid-template-columns:1fr}.files{height:300px}.filters{flex-direction:column}}
</style>
</head>
<body><main class="shell">
<h1>Mac Cleaner</h1>
<p class="lead">Recover space safely. Recommended clutter is one click; important files stay protected.</p>
<section class="toolbar">
  <div class="field"><label for="folders">Folders to scan (separate with commas)</label><input id="folders"></div>
  <div class="field age"><label for="preset">Preset</label><select id="preset"><option>conservative</option><option selected>balanced</option><option>aggressive</option></select></div>
  <div class="field age"><label for="age">Minimum age</label><select id="age"><option>1</option><option selected>7</option><option>14</option><option>30</option><option>60</option><option>90</option></select></div>
  <button class="button primary" id="scan">Scan now</button>
  <button class="button" id="cancel" disabled>Cancel</button>
</section>
<details class="advanced"><summary>Advanced detectors</summary><p><label><input type="checkbox" id="duplicates" checked> Exact duplicates</label><label><input type="checkbox" id="emptyFolders"> Empty folders</label><label><input type="checkbox" id="developerCaches"> Developer caches</label><label><input type="checkbox" id="iphoneBackups"> iPhone backups</label><label><input type="checkbox" id="icloudDrive"> iCloud Drive audit</label></p><p class="meta">iCloud results are always protected and Trash-only. Removing one also removes it from every synced device.</p></details>
<section class="summary"><strong id="summary">Ready to scan</strong><span class="status" id="status">Local and private</span></section>
<section class="filters"><input id="search" placeholder="Filter by name, path, category…"><select id="sort"><option value="size">Largest first</option><option value="confidence">Highest confidence</option><option value="age">Oldest first</option><option value="name">Name</option></select></section>
<div class="grid">
  <section class="panel"><div class="panel-head"><h2>Recommended cleanup</h2><p>Old, recognizable clutter</p></div><div class="files" id="recommended"><div class="empty">Scan to find clutter</div></div><div class="panel-actions"><button class="button primary" id="cleanRecommended" disabled>Clean recommended files</button></div></section>
  <section class="panel"><div class="panel-head"><h2>Needs review</h2><p>Recent or unusually large files</p></div><div class="review-note">These files are never included automatically.</div><div class="files" id="review"><div class="empty">Protected files will appear here</div></div><div class="panel-actions"><button class="button" id="cleanReview" disabled>Clean selected review files…</button></div></section>
</div>
<div class="foot"><label><input type="checkbox" id="permanent"> Permanently delete instead of moving to Trash</label><button class="button" id="quit">Stop GUI server</button></div>
<details class="history"><summary>Recent cleanup history</summary><div id="history">Loading…</div></details>
</main><div class="toast" id="toast"></div>
<script>
const TOKEN=__TOKEN__;
const $=id=>document.getElementById(id);
let recommended=[],review=[];
async function api(path,body={}){const response=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json','X-Mac-Cleaner-Token':TOKEN},body:JSON.stringify(body)});const data=await response.json();if(!response.ok)throw new Error(data.error||'Request failed');return data}
async function getApi(path){const response=await fetch(path,{headers:{'X-Mac-Cleaner-Token':TOKEN}});const data=await response.json();if(!response.ok)throw new Error(data.error||'Request failed');return data}
function esc(value){const node=document.createElement('span');node.textContent=value;return node.innerHTML}
function row(item,checkbox=false){const cloud=item.is_icloud?'☁ iCloud · ':'';const local=item.is_icloud&&item.local_size!==null?` · ${esc(item.local_size)} on this Mac`:'';return `<div class="file">${checkbox?`<input class="check review-check" type="checkbox" value="${item.id}">`:'<span class="safe">✓</span>'}<div><div class="file-name">${cloud}${esc(item.name)}</div><div class="meta">${esc(item.category)} · ${item.confidence}% confidence · ${esc(item.reason)} · ${item.age_days} days old<br>${esc(item.signals.join(', '))}<br>${esc(item.path)}</div><button class="button preview" onclick="quicklook('${item.id}')">Quick Look</button></div><div class="size">${esc(item.size)}${local}</div></div>`}
function visible(items){const query=$('search').value.trim().toLowerCase();const result=items.filter(x=>!query||`${x.name} ${x.path} ${x.category} ${x.reason}`.toLowerCase().includes(query));const mode=$('sort').value;result.sort((a,b)=>mode==='confidence'?b.confidence-a.confidence:mode==='age'?b.age_days-a.age_days:mode==='name'?a.name.localeCompare(b.name):b.bytes-a.bytes);return result}
function render(){const shownRecommended=visible(recommended),shownReview=visible(review);$('recommended').innerHTML=shownRecommended.length?shownRecommended.map(x=>row(x)).join(''):'<div class="empty">No matching recommended clutter</div>';$('review').innerHTML=shownReview.length?shownReview.map(x=>row(x,true)).join(''):'<div class="empty">No matching protected files</div>';$('cleanRecommended').disabled=!recommended.length;$('cleanReview').disabled=true;document.querySelectorAll('.review-check').forEach(x=>x.addEventListener('change',()=>{$('cleanReview').disabled=!document.querySelectorAll('.review-check:checked').length}))}
function toast(text){$('toast').textContent=text;$('toast').style.display='block';setTimeout(()=>$('toast').style.display='none',4500)}
let progressTimer=null;
async function updateProgress(){try{const data=await getApi('/api/progress');if(data.active)$('status').textContent=`Scanning ${data.inspected} files… ${data.folder}` }catch(e){}}
async function doScan(){try{$('scan').disabled=true;$('cancel').disabled=false;$('status').textContent='Scanning…';progressTimer=setInterval(updateProgress,500);const data=await api('/api/scan',{folders:$('folders').value.split(',').map(x=>x.trim()).filter(Boolean),min_age:Number($('age').value),preset:$('preset').value,duplicates:$('duplicates').checked,empty_folders:$('emptyFolders').checked,developer_caches:$('developerCaches').checked,iphone_backups:$('iphoneBackups').checked,icloud_drive:$('icloudDrive').checked});recommended=data.recommended;review=data.review;const cloud=data.icloud_count?` · ${data.icloud_count} iCloud`:'';$('summary').textContent=`${recommended.length} recommended · ${review.length} need review${cloud} · ${data.total_size} found`;$('status').textContent=data.warnings.length?`${data.warnings.length} warning(s)`:'Scan complete';render();if(data.warnings.length)toast(data.warnings.join('\n'))}catch(e){toast(e.message);$('status').textContent='Scan failed'}finally{clearInterval(progressTimer);$('scan').disabled=false;$('cancel').disabled=true}}
$('scan').onclick=doScan;
$('cancel').onclick=()=>api('/api/cancel');
$('search').oninput=render;$('sort').onchange=render;
async function quicklook(id){try{await api('/api/quicklook',{id})}catch(e){toast(e.message)}}
async function clean(ids,containsReview){const permanent=$('permanent').checked;const chosen=[...recommended,...review].filter(x=>ids.includes(x.id));const cloud=chosen.filter(x=>x.is_icloud);let permanentConfirmation='',icloudConfirmation='';if(permanent&&cloud.length){toast('iCloud Drive items are Trash-only; turn off permanent deletion.');return}if(permanent){permanentConfirmation=prompt(`Permanent deletion cannot be undone.\n\nType DELETE to permanently delete ${ids.length} selected file(s):`)||'';if(permanentConfirmation!=='DELETE')return}else if(containsReview){if(!confirm(`These ${chosen.length} protected file(s) are recent, large, or cloud-synced. Move them to Trash?\n\n${chosen.slice(0,8).map(x=>'• '+x.name).join('\n')}`))return}if(cloud.length){icloudConfirmation=prompt(`CAUTION: removing ${cloud.length} iCloud Drive item(s) removes them from every synced device.\n\nType ICLOUD to continue:`)||'';if(icloudConfirmation!=='ICLOUD')return}try{$('status').textContent=permanent?'Deleting permanently…':'Moving to Trash…';const data=await api('/api/clean',{ids,confirm_review:containsReview,permanent,confirm_permanent:permanentConfirmation,confirm_icloud:icloudConfirmation});toast(`${data.action} ${data.changed} file(s), ${data.size}`);await doScan();await loadHistory()}catch(e){toast(e.message)}}
$('cleanRecommended').onclick=()=>clean(recommended.map(x=>x.id),false);
$('cleanReview').onclick=()=>clean([...document.querySelectorAll('.review-check:checked')].map(x=>x.value),true);
$('quit').onclick=async()=>{await api('/api/shutdown');document.body.innerHTML='<main class="shell"><h1>Mac Cleaner stopped</h1><p class="lead">You can close this tab.</p></main>'};
async function loadHistory(){try{const data=await getApi('/api/history');$('history').innerHTML=data.entries.length?data.entries.map(x=>`<div class="history-row"><strong>${esc(x.action)}</strong> · ${esc(String(x.size))} bytes · ${esc(x.timestamp)}<br>${esc(x.original_path)}</div>`).join(''):'No cleanup history yet.'}catch(e){$('history').textContent='History unavailable.'}}
getApi('/api/config').then(data=>{$('folders').value=data.folders.join(', ');doScan()});loadHistory();
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
        "confidence": item.confidence, "category": item.category,
        "signals": list(item.signals),
        "kind": item.kind, "trash_only": item.trash_only,
        "is_icloud": item.is_icloud,
        "local_size": human_size(item.local_size) if item.local_size is not None else None,
        "bytes": item.size,
    }


class CleanerServer(ThreadingHTTPServer):
    def __init__(self, token: str) -> None:
        super().__init__(("127.0.0.1", 0), CleanerHandler)
        self.token = token
        self.items: dict[str, Candidate] = {}
        self.lock = threading.Lock()
        self.cancel_event = threading.Event()
        self.progress: dict[str, Any] = {"active": False, "inspected": 0, "folder": ""}


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
            self._json(200, {
                "folders": [str(path) for path in default_folders()],
                "icloud_drive": str(icloud_drive_folder()),
                "icloud_available": icloud_drive_folder().is_dir(),
            })
        elif parsed.path == "/api/progress":
            with self.server.lock:
                self._json(200, dict(self.server.progress))
        elif parsed.path == "/api/history":
            self._json(200, {"entries": read_history()})
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
            elif path == "/api/cancel":
                self.server.cancel_event.set()
                self._json(200, {"ok": True})
            elif path == "/api/quicklook":
                self._quicklook(data)
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
        preset = str(data.get("preset", "balanced"))
        rules = load_rules(preset_override=preset)
        rules = replace(
            rules,
            detect_duplicates=bool(data.get("duplicates", rules.detect_duplicates)),
            detect_empty_folders=bool(data.get("empty_folders", False)),
        )
        self.server.cancel_event.clear()
        with self.server.lock:
            self.server.progress = {"active": True, "inspected": 0, "folder": ""}

        def progress(inspected: int, folder: Path) -> None:
            with self.server.lock:
                self.server.progress = {
                    "active": True, "inspected": inspected,
                    "folder": "" if folder == Path(".") else str(folder),
                }

        try:
            scan_roots = folders or default_folders()
            if bool(data.get("icloud_drive", False)):
                scan_roots = [*scan_roots, icloud_drive_folder()]
            candidates, warnings = scan(
                scan_roots, minimum_age, rules, progress, self.server.cancel_event
            )
            if not self.server.cancel_event.is_set():
                candidates.extend(special_storage_candidates(
                    bool(data.get("developer_caches", False)), bool(data.get("iphone_backups", False))
                ))
            candidates.sort(key=lambda item: item.size, reverse=True)
        finally:
            with self.server.lock:
                self.server.progress["active"] = False
        recommended, review = partition_candidates(candidates)
        with self.server.lock:
            self.server.items = {item_id(item): item for item in candidates}
        self._json(200, {
            "recommended": [serialize(item) for item in recommended],
            "review": [serialize(item) for item in review],
            "total_size": human_size(sum(item.size for item in candidates)),
            "icloud_count": sum(item.is_icloud for item in candidates),
            "icloud_size": human_size(sum(item.size for item in candidates if item.is_icloud)),
            "warnings": warnings,
        })

    def _quicklook(self, data: dict[str, Any]) -> None:
        identifier = data.get("id")
        with self.server.lock:
            item = self.server.items.get(identifier)
        if item is None or not item.path.exists():
            raise ValueError("File is no longer available; scan again")
        subprocess.Popen(
            ["/usr/bin/qlmanage", "-p", str(item.path)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        self._json(200, {"ok": True})

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
        permanent = data.get("permanent") is True
        if permanent and any(item.is_icloud for item in selected):
            raise ValueError("iCloud Drive items cannot be permanently deleted; use Trash")
        if permanent and data.get("confirm_permanent") != "DELETE":
            raise ValueError("Permanent deletion requires typing DELETE")
        if any(item.is_icloud for item in selected) and data.get("confirm_icloud") != "ICLOUD":
            raise ValueError("iCloud Drive cleanup requires typing ICLOUD")
        if permanent:
            changed, bytes_changed, errors = delete_permanently(selected)
            action = "Permanently deleted"
        else:
            changed, bytes_changed, errors = move_to_trash(selected)
            action = "Moved to Trash"
        if errors:
            raise ValueError("\n".join(errors))
        self._json(200, {"changed": changed, "size": human_size(bytes_changed), "action": action})


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
