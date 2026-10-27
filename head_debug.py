import json
import math
import os
import queue
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit


DEFAULT_CONFIG = {
    "both_center": 0.0,
    "both_range": 6.0,
    "left_center": 25.0,
    "left_range": 10.0,
    "right_center": -25.0,
    "right_range": 10.0,
    "test_mode": "auto",
}
CONFIG_PATH = Path(__file__).resolve().parent / "head_calibration.json"
VALID_TEST_MODES = {"auto", "both", "left", "right", "none"}


def normalize_config(data):
    config = DEFAULT_CONFIG.copy()
    if isinstance(data, dict):
        for name in (
            "both_center",
            "left_center",
            "right_center",
            "both_range",
            "left_range",
            "right_range",
        ):
            try:
                value = float(data.get(name, config[name]))
                if not math.isfinite(value):
                    raise ValueError
                if name.endswith("_center"):
                    value = max(-90.0, min(90.0, value))
                else:
                    value = max(0.5, min(90.0, value))
                config[name] = value
            except (TypeError, ValueError):
                pass
        if isinstance(data.get("test_mode"), str) and data["test_mode"] in VALID_TEST_MODES:
            config["test_mode"] = data["test_mode"]
    return config


def load_calibration_config(path=CONFIG_PATH):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, UnicodeDecodeError, json.JSONDecodeError):
        return DEFAULT_CONFIG.copy()
    return normalize_config(data)


PAGE = r'''<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>头向检测调试</title>
<style>
*{box-sizing:border-box}body{margin:0;background:#111820;color:#e8edf2;font:15px "Microsoft YaHei UI",sans-serif}
main{max-width:1180px;margin:0 auto;padding:22px}h1{font-size:22px;margin:0 0 16px}
.grid{display:grid;grid-template-columns:minmax(0,1.65fr) minmax(300px,1fr);gap:16px}
section{background:#1b252f;border:1px solid #34414d;border-radius:6px;padding:14px;margin-bottom:16px}
img{display:block;width:100%;min-height:240px;max-height:72vh;object-fit:contain;background:#090d11}
.metrics{display:grid;grid-template-columns:1fr 1fr;gap:10px}.metric{padding:12px;background:#111820;border-radius:4px}.label{font-size:12px;color:#9aa8b5}
.value{font:23px Consolas,monospace;margin-top:6px;overflow-wrap:anywhere}#direction{font:28px "Microsoft YaHei UI",sans-serif}
footer{color:#9aa8b5;margin-top:14px;font-size:13px}.config h2{font-size:16px;margin:0 0 12px}.row{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin:8px 0}
label{color:#c7d0d8;font-size:13px}input,button{font:inherit;border:1px solid #52616e;border-radius:4px;padding:7px;background:#111820;color:#e8edf2}
input{width:100%;margin-top:4px}button{cursor:pointer;background:#263746}button:hover{background:#344b5f}.buttons{display:flex;flex-wrap:wrap;gap:8px;margin-top:12px}.hint{color:#9aa8b5;font-size:12px;margin-top:10px}
@media(max-width:800px){.grid{grid-template-columns:1fr}main{padding:12px}}
</style>
</head>
<body><main>
<h1>头部方向检测调试</h1>
<div class="grid">
<div>
<section><img id="frame" alt="实时摄像头画面"><footer id="connection">等待检测帧…</footer></section>
<section class="config"><h2>屏幕中心点和范围</h2>
<div class="row"><label>两屏都亮中心 yaw<input id="both_center" type="number" step="0.1"></label><label>范围<input id="both_range" type="number" step="0.1" min="0.5"></label></div>
<div class="row"><label>左屏亮中心 yaw<input id="left_center" type="number" step="0.1"></label><label>范围<input id="left_range" type="number" step="0.1" min="0.5"></label></div>
<div class="row"><label>右屏亮中心 yaw<input id="right_center" type="number" step="0.1"></label><label>范围<input id="right_range" type="number" step="0.1" min="0.5"></label></div>
<div class="buttons"><button onclick="saveConfig()">保存配置</button><button onclick="setCurrent('both')">当前 yaw → 两屏中心</button><button onclick="setCurrent('left')">当前 yaw → 左屏中心</button><button onclick="setCurrent('right')">当前 yaw → 右屏中心</button></div>
<div class="buttons"><button onclick="setTest('auto')">自动判定</button><button onclick="setTest('both')">测试两屏亮</button><button onclick="setTest('left')">测试左屏亮</button><button onclick="setTest('right')">测试右屏亮</button><button onclick="setTest('none')">测试全遮罩</button></div>
<div class="hint">范围外为全遮罩。当前 yaw 使用有效 yaw（包含左右反转设置）。</div></section>
</div>
<section><div class="metrics">
<div class="metric"><div class="label">屏幕模式</div><div class="value" id="direction">等待</div></div>
<div class="metric"><div class="label">当前候选</div><div class="value" id="candidate">-</div></div>
<div class="metric"><div class="label">原始 yaw</div><div class="value" id="yaw">-</div></div>
<div class="metric"><div class="label">有效 yaw</div><div class="value" id="effective_yaw">-</div></div>
<div class="metric"><div class="label">平滑 yaw</div><div class="value" id="smooth">-</div></div>
<div class="metric"><div class="label">pitch</div><div class="value" id="pitch">-</div></div>
<div class="metric"><div class="label">roll</div><div class="value" id="roll">-</div></div>
<div class="metric"><div class="label">候选稳定次数</div><div class="value" id="stable">-</div></div>
<div class="metric"><div class="label">连续未检测到脸</div><div class="value" id="missing">-</div></div>
<div class="metric"><div class="label">YOLO 人数</div><div class="value" id="person_count">-</div></div>
<div class="metric"><div class="label">多人触发进度</div><div class="value" id="person_progress">-</div></div>
<div class="metric"><div class="label">本人状态</div><div class="value" id="face_state">-</div></div>
<div class="metric"><div class="label">人脸相似度</div><div class="value" id="face_score">-</div></div>
<div class="metric"><div class="label">测试模式</div><div class="value" id="test_mode">auto</div></div>
</div><footer>仅显示本机检测进程的数据；页面不会自行访问摄像头。</footer></section>
</div></main>
<script>
const fields=["direction","candidate","yaw","effective_yaw","smooth","pitch","roll","stable","missing","person_count","face_state","face_score","test_mode"];
const names={both:"两屏亮",left:"左屏亮",right:"右屏亮",none:"全遮罩",unknown:"未识别",auto:"自动"};
let latest={};
let currentTestMode="auto";
function setForm(c){for(const key of ["both_center","both_range","left_center","left_range","right_center","right_range"]){if(c[key]!==undefined)document.getElementById(key).value=c[key]}}
async function loadConfig(){const response=await fetch("/config");const data=await response.json();setForm(data);currentTestMode=data.test_mode;document.getElementById("test_mode").textContent=names[data.test_mode]||data.test_mode}
async function saveConfig(extra={}){const data={};for(const key of ["both_center","both_range","left_center","left_range","right_center","right_range"]){data[key]=Number(document.getElementById(key).value)}Object.assign(data,{test_mode:currentTestMode},extra);const response=await fetch("/config",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(data)});if(!response.ok){alert("配置保存失败");return}const saved=await response.json();setForm(saved);currentTestMode=saved.test_mode;document.getElementById("test_mode").textContent=names[saved.test_mode]||saved.test_mode}
function setCurrent(name){if(latest.effective_yaw===null||latest.effective_yaw===undefined){alert("还没有有效 yaw");return}document.getElementById(name+"_center").value=latest.effective_yaw;saveConfig()}
function setTest(mode){currentTestMode=mode;saveConfig({test_mode:mode})}
loadConfig().catch(()=>{});
const source=new EventSource("/events");
source.onopen=()=>document.getElementById("connection").textContent="已连接到本机检测进程";
source.onerror=()=>document.getElementById("connection").textContent="连接中断，等待检测进程…";
source.onmessage=(event)=>{const d=JSON.parse(event.data);latest=d;
 for(const key of fields){const node=document.getElementById(key);if(d[key]!==undefined)node.textContent=d[key]}
  if(d.mode)document.getElementById("direction").textContent=names[d.mode]||d.mode;
  if(d.candidate)document.getElementById("candidate").textContent=names[d.candidate]||d.candidate;
  if(d.test_mode){currentTestMode=d.test_mode;document.getElementById("test_mode").textContent=names[d.test_mode]||d.test_mode}
  if(d.person_frames!==undefined)document.getElementById("person_progress").textContent=`${d.person_frames}/${d.trigger_frames}`;
  if(d.frame)document.getElementById("frame").src="data:image/jpeg;base64,"+d.frame;
 document.getElementById("connection").textContent=`帧 ${d.frame_number} · ${d.width}×${d.height} · ${d.fps} FPS`;
};
</script></body></html>'''


class HeadDebugServer:
    def __init__(self, host="127.0.0.1", port=8765, config_path=CONFIG_PATH):
        self.clients = []
        self.lock = threading.Lock()
        self.config_lock = threading.Lock()
        self.config_path = Path(config_path)
        self.config = load_calibration_config(self.config_path)
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def handle(self):
                try:
                    super().handle()
                except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError, OSError):
                    pass

            def send_json(self, payload, status=200):
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                path = urlsplit(self.path).path
                if path == "/":
                    body = PAGE.encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                if path == "/config":
                    self.send_json(owner.get_config())
                    return
                if path != "/events":
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
                except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError, OSError):
                    pass
                finally:
                    with owner.lock:
                        if messages in owner.clients:
                            owner.clients.remove(messages)

            def do_POST(self):
                if urlsplit(self.path).path != "/config":
                    self.send_error(404)
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 0 < length <= 16384:
                        self.send_json({"error": "invalid request size"}, 413)
                        return
                    data = json.loads(self.rfile.read(length).decode("utf-8"))
                except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
                    self.send_json({"error": "invalid JSON"}, 400)
                    return
                self.send_json(owner.update_config(data))

            def log_message(self, _format, *_args):
                return

        self.server = ThreadingHTTPServer((host, port), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://{host}:{self.server.server_port}/"

    def get_config(self):
        with self.config_lock:
            return self.config.copy()

    def update_config(self, data):
        config = normalize_config(data)
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        with self.config_lock:
            descriptor, name = tempfile.mkstemp(
                prefix="head_calibration_", suffix=".json", dir=self.config_path.parent
            )
            os.close(descriptor)
            temporary = Path(name)
            try:
                temporary.write_text(
                    json.dumps(config, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
                temporary.replace(self.config_path)
                self.config = config
            finally:
                if temporary.exists():
                    temporary.unlink()
        return config.copy()

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
