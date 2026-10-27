import json
import queue
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


PAGE = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>头向检测调试</title>
<style>
*{box-sizing:border-box}body{margin:0;background:#111820;color:#e8edf2;font:15px "Microsoft YaHei UI",sans-serif}
main{max-width:1120px;margin:0 auto;padding:22px}h1{font-size:22px;margin:0 0 16px}
.grid{display:grid;grid-template-columns:minmax(0,1.7fr) minmax(260px,1fr);gap:16px}
section{background:#1b252f;border:1px solid #34414d;border-radius:6px;padding:14px}
img{display:block;width:100%;min-height:240px;max-height:72vh;object-fit:contain;background:#090d11}
.metrics{display:grid;grid-template-columns:1fr 1fr;gap:10px}
.metric{padding:12px;background:#111820;border-radius:4px}.label{font-size:12px;color:#9aa8b5}
.value{font:24px Consolas,monospace;margin-top:6px;overflow-wrap:anywhere}
#direction{font:28px "Microsoft YaHei UI",sans-serif}
footer{color:#9aa8b5;margin-top:14px;font-size:13px}
@media(max-width:760px){.grid{grid-template-columns:1fr}main{padding:12px}}
</style>
</head>
<body><main>
<h1>头部方向检测调试</h1>
<div class="grid">
<section><img id="frame" alt="实时摄像头画面"><footer id="connection">等待检测帧…</footer></section>
<section><div class="metrics">
<div class="metric"><div class="label">稳定方向</div><div class="value" id="direction">等待</div></div>
<div class="metric"><div class="label">当前候选</div><div class="value" id="candidate">-</div></div>
<div class="metric"><div class="label">原始 yaw</div><div class="value" id="yaw">-</div></div>
<div class="metric"><div class="label">平滑 yaw</div><div class="value" id="smooth">-</div></div>
<div class="metric"><div class="label">pitch</div><div class="value" id="pitch">-</div></div>
<div class="metric"><div class="label">roll</div><div class="value" id="roll">-</div></div>
<div class="metric"><div class="label">候选稳定次数</div><div class="value" id="stable">-</div></div>
<div class="metric"><div class="label">连续未检测到脸</div><div class="value" id="missing">-</div></div>
</div><footer>仅显示本机检测进程的数据；页面不会自行访问摄像头。</footer></section>
</div></main>
<script>
const fields=["direction","candidate","yaw","smooth","pitch","roll","stable","missing"];
const names={left:"左",right:"右",center:"中间",unknown:"未识别"};
const source=new EventSource("/events");
source.onopen=()=>document.getElementById("connection").textContent="已连接到本机检测进程";
source.onerror=()=>document.getElementById("connection").textContent="连接中断，等待检测进程…";
source.onmessage=(event)=>{const d=JSON.parse(event.data);
 for(const key of fields){const node=document.getElementById(key);if(d[key]!==undefined)node.textContent=d[key]}
 if(d.direction)document.getElementById("direction").textContent=names[d.direction]||d.direction;
 if(d.frame)document.getElementById("frame").src="data:image/jpeg;base64,"+d.frame;
 document.getElementById("connection").textContent=`帧 ${d.frame_number} · ${d.width}×${d.height} · ${d.fps} FPS`;
};
</script></body></html>"""


class HeadDebugServer:
    def __init__(self, host="127.0.0.1", port=8765):
        self.clients = []
        self.lock = threading.Lock()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == "/":
                    body = PAGE.encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                if self.path != "/events":
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "keep-alive")
                self.end_headers()
                messages = queue.Queue(maxsize=2)
                with owner.lock:
                    owner.clients.append(messages)
                try:
                    self.wfile.write(b": connected\n\n")
                    self.wfile.flush()
                    while True:
                        try:
                            payload = messages.get(timeout=15)
                            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                            self.wfile.write(b"data: " + data + b"\n\n")
                        except queue.Empty:
                            self.wfile.write(b": keepalive\n\n")
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, OSError):
                    pass
                finally:
                    with owner.lock:
                        if messages in owner.clients:
                            owner.clients.remove(messages)

            def log_message(self, _format, *_args):
                return

        self.server = ThreadingHTTPServer((host, port), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://{host}:{self.server.server_port}/"

    def publish(self, payload):
        with self.lock:
            clients = tuple(self.clients)
        for client in clients:
            if client.full():
                try:
                    client.get_nowait()
                except queue.Empty:
                    pass
            try:
                client.put_nowait(payload)
            except queue.Full:
                pass

    def close(self):
        self.server.shutdown()
        self.server.server_close()
