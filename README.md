# Moyu YOLO

一个运行在 Windows 上的摄像头环境提醒工具：使用 YOLO 检测摄像头画面中的人物，当检测到身后有人经过时，先用屏幕边缘红框即时提醒，再切换到用户选择的窗口。

项目适合个人桌面环境使用，也可以作为 YOLO 人体检测、Windows 窗口激活、PySide6 GUI 和摄像头视频流处理的示例项目。

> 重要：请只在获得现场人员同意、符合公司规定和当地法律的前提下使用摄像头。项目默认会把触发帧保存到本地 `captures/`，这些图片可能包含他人影像。

## 功能

- 使用 Ultralytics YOLO 进行 `person` 类别检测。
- 通过背景区域、人物位置和连续帧运动判断“有人经过”。
- 自动排除画面下方中央、靠近镜头的大型人物框，减少把摄像头使用者识别为路人。
- 选择当前已打开的 Windows 窗口作为切换目标。
- 使用 Windows 窗口句柄绑定目标，因此 VS Code 文件名、扩展名或项目标题变化后仍然可以切回原窗口。
- 目标窗口关闭并重新打开后，可以通过“刷新”重新选择。
- 触发时保存一张带检测框和 `TRIGGER` 标记的 JPG。
- GUI 中可以浏览并打开 `captures/` 中的历史触发截图。
- 默认使用 PySide6 绘制屏幕边缘红框提醒，避免 Windows Toast 通知延迟。
- Windows 通知改为可选项，默认关闭；开启后发送低暴露度通知：
  - 标题：`状态更新`
  - 内容：`请确认周围情况`
- PySide6 GUI 使用 `pythonw.exe` 启动，不弹出黑色控制台窗口。
- GUI 可以配置连续触发帧数、最小移动像素、YOLO 置信度、冷却时间、背景区域和正脸辅助触发。
- 支持摄像头实时检测、录制视频回放和 dry-run 调试。

## 工作流程

```text
摄像头/视频
    |
    v
YOLO person 检测 + ByteTrack 跟踪
    |
    v
背景区域判断、摄像头使用者排除、人物运动判断
    |
    v
连续达到触发帧数
    |
    +--> 保存触发帧到 captures/
    +--> GUI 立即显示屏幕边缘红框
    +--> 激活选中的 Windows 窗口
    +--> （可选）GUI 发送静音 Windows 通知
```

## 项目结构

```text
.
├── app.py                   # PySide6 图形界面
├── detect_camera.py         # 摄像头、视频回放和检测逻辑
├── test_detection_logic.py  # 检测逻辑单元测试
├── requirements.txt         # Python 依赖
├── weights/
│   └── yolo26n.pt           # 本地模型权重，不提交到 Git
├── captures/                # 触发截图，不提交到 Git
└── 行人视频.mp4             # 本地测试视频，不提交到 Git
```

## 环境要求

- Windows 10 或 Windows 11
- Python 3.9 及以上
- 可用摄像头
- Windows 通知功能没有被系统策略完全禁用
- 首次运行时需要下载或准备 YOLO 模型权重

项目针对 Windows 编写，窗口枚举、窗口激活和 Windows 通知功能在 Linux/macOS 上不适用。

## 安装

### 1. 克隆仓库

```powershell
git clone https://github.com/Sixstones1/moyu-yolo.git
Set-Location moyu-yolo
```

### 2. 创建虚拟环境

```powershell
py -3 -m venv .venv
```

### 3. 安装依赖

```powershell
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

如果 Windows 拒绝把 Qt DLL 写入虚拟环境，可以把 PySide6 安装到项目隔离目录：

```powershell
.\.venv\Scripts\python.exe -m pip install ultralytics==8.4.189 opencv-python==4.11.0.86 lap==0.5.13 winotify==1.1.0
.\.venv\Scripts\python.exe -m pip install --target .qt_packages PySide6==6.6.3
```

`app.py` 会自动优先加载项目内的 `.qt_packages`。

### 4. 准备模型

默认模型路径为：

```text
weights/yolo26n.pt
```

可以使用 Ultralytics 下载模型，再放入 `weights/`：

```powershell
New-Item -ItemType Directory -Force weights | Out-Null
.\.venv\Scripts\python.exe -c "from ultralytics import YOLO; YOLO('yolo26n.pt')"
Move-Item .\yolo26n.pt .\weights\yolo26n.pt
```

模型权重被 `.gitignore` 忽略，不应该提交到公共仓库。

## 启动图形界面

推荐使用 `pythonw.exe`，这样不会弹出黑色 CMD 窗口：

```powershell
.\.venv\Scripts\pythonw.exe app.py
```

GUI 使用方式：

1. 启动程序。
2. 在“切换目标”下拉框中选择已经打开的应用窗口。
3. 选择摄像头编号，通常默认 `0`。
4. 可以先点击“测试切换”确认目标窗口可被激活。
5. 点击“开始监测”。
6. 在检测参数区域调整连续触发帧数、移动像素、置信度、冷却秒数和背景区域。
7. 默认只检测“移动经过”，不会因为你静止看屏幕而触发；只有勾选“正脸辅助触发”后，正面人脸才会参与触发。
8. 检测触发后会先保存截图并显示红色屏幕边框，然后切换到目标窗口。
9. 在“触发截图历史”中选择任意历史截图，点击“打开”查看；新截图会自动选中。
10. 如果需要 Windows 通知，再勾选“Windows 通知”；系统通知可能仍受 Windows 通知中心延迟影响。
11. 点击“停止监测”结束摄像头子进程。

如果目标应用完全关闭后重新打开，Windows 窗口句柄会发生变化，需要点击“刷新”并重新选择窗口。

## 命令行运行

### 直接打开摄像头

```powershell
.\.venv\Scripts\python.exe detect_camera.py
```

命令行模式会使用摄像头 `0`，默认寻找标题中包含 `Visual Studio Code` 的窗口。

### Dry-run

Dry-run 会运行检测逻辑，但不会真正激活目标窗口：

```powershell
.\.venv\Scripts\python.exe detect_camera.py --dry-run
```

### 无预览运行有限帧

```powershell
.\.venv\Scripts\python.exe detect_camera.py --dry-run --no-preview --max-frames 100
```

### 回放录制视频

```powershell
.\.venv\Scripts\python.exe detect_camera.py `
  --video .\行人视频.mp4 `
  --dry-run `
  --no-preview
```

录制视频属于本地测试素材，默认不会被提交到 Git。

### 指定目标窗口

```powershell
.\.venv\Scripts\python.exe detect_camera.py `
  --ide-title "PyCharm" `
  --camera 0
```

### 常用调参

```powershell
.\.venv\Scripts\python.exe detect_camera.py `
  --camera 0 `
  --confidence 0.45 `
  --trigger-frames 8 `
  --movement-pixels 24 `
  --cooldown 15 `
  --zone-bottom 0.65
```

参数说明：

| 参数 | 默认值 | 说明 |
| --- | ---: | --- |
| `--camera` | `0` | 摄像头编号 |
| `--video` | 无 | 使用视频文件代替摄像头 |
| `--model` | `weights/yolo26n.pt` | YOLO 模型路径 |
| `--ide-title` | `Visual Studio Code` | 找不到窗口句柄时使用的标题匹配文本 |
| `--ide-hwnd` | `0` | GUI 选择的 Windows 窗口句柄 |
| `--confidence` | `0.35` | YOLO 置信度阈值 |
| `--trigger-frames` | `6` | 连续满足触发条件的帧数；30 FPS 时约为 0.2 秒 |
| `--movement-pixels` | `18` | 跟踪框中心累计横向或纵向移动达到的最小像素数 |
| `--cooldown` | `10` | 两次触发之间的冷却时间，单位为秒 |
| `--zone-bottom` | `0.72` | 背景检测区域底边占画面高度的比例 |
| `--look-trigger` | 关闭 | 额外启用非本人正面人脸触发；默认关闭以避免看屏幕误触发 |
| `--capture-dir` | `captures` | 触发截图保存目录 |
| `--no-preview` | 关闭 | 不显示 OpenCV 预览窗口 |
| `--dry-run` | 关闭 | 只报告触发，不激活窗口 |
| `--max-frames` | `0` | 指定帧数后退出，`0` 表示持续运行 |

## 检测逻辑说明

当前实现不是重新训练一个“身后有人”专用模型，而是使用预训练的人体检测模型，再通过规则组合得到触发条件：

1. YOLO 只保留 `person` 类别。
2. ByteTrack 为连续帧中的人物分配跟踪 ID。
3. 只关注画面上方背景区域，默认不关注画面底部的主体区域。
4. 如果人物跟踪中心在连续观测中移动至少 `--movement-pixels` 像素，则认为可能经过。
5. 居中、体积较大并且接触画面底边的人物框会被视为摄像头使用者并排除。
6. 默认只有“经过”信号会触发；`--look-trigger` 打开后，非本人正面人脸也可作为信号。
7. 连续达到 `--trigger-frames` 后才触发，减少单帧误检。
8. 触发后进入 `--cooldown` 冷却时间。

### “多少帧后触发”

实际触发条件是：连续 `--trigger-frames` 帧检测到触发信号。GUI 默认是 6 帧，摄像头约 30 FPS 时理论上约 0.2 秒；如果画面抖动或人物框不稳定，建议从 8 到 12 帧开始测试。程序触发时会在运行记录中输出实际使用的连续帧数。

项目还保留了正面人脸近似判断，用于辅助判断非本人是否朝向摄像头。它不是眼球视线识别，侧脸、遮挡、低分辨率和远距离人物可能无法判断。

## 触发截图

每次触发都会生成类似下面的文件：

```text
captures/trigger_20261026_165605_378296.jpg
```

截图包含：

- YOLO 人物检测框
- `person` 置信度
- `passing` 或 `looking` 标记
- 红色 `TRIGGER` 标记

`captures/` 已加入 `.gitignore`，不会被提交到公共仓库。
GUI 启动时会自动读取已有 JPG，并按修改时间倒序排列；点击“刷新”可重新扫描目录。

## 提醒方式

### 屏幕边缘红框

默认提醒由 GUI 直接创建置顶、透明、不可接收输入的 PySide6 窗口，并在每个显示器边缘绘制红框，显示约 0.9 秒。触发日志会在窗口激活前输出，因此红框不需要等待 Windows 通知中心。

这个实现不依赖第三方红边项目。若以后需要更底层或更强的系统级效果，可以改用 Win32 layered window；Windows App SDK / WinUI 3 也能做类似的桌面提醒，但需要额外的打包和运行时配置，不适合当前工具。

### Windows 通知

通知由 PySide6 GUI 进程发送，检测子进程只负责输出触发日志。它是可选的，默认关闭。这样可以：

- 保持检测进程简单；
- 继续在 GUI 中显示日志；
- 使用静音通知，降低暴露度；
- 通知标题不直接使用“身后有人”。

如果看不到通知，请检查：

1. Windows 设置中的“通知”是否启用。
2. “系统状态”或相关通知来源是否被关闭。
3. 勿扰模式、专注助手或组织策略是否阻止通知。
4. GUI 是否使用 `pythonw.exe` 启动。

## 测试

运行全部单元测试：

```powershell
.\.venv\Scripts\python.exe -m unittest -v
```

检查语法：

```powershell
.\.venv\Scripts\python.exe -m py_compile app.py detect_camera.py test_detection_logic.py
```

检查依赖：

```powershell
.\.venv\Scripts\python.exe -m pip check
```

测试覆盖包括：

- 背景区域判断；
- 摄像头使用者排除；
- 经过人物的运动阈值；
- 静止人物不触发；
- 正面人脸辅助判断；
- 正脸辅助触发默认关闭的逻辑；
- 触发截图生成；
- VS Code 动态标题的稳定后缀匹配。

## 隐私与安全

- 这个工具会读取摄像头画面，请在使用前确认摄像头权限。
- 触发截图可能包含同事、访客或办公环境信息。
- 不要把 `行人视频.mp4`、`captures/` 或任何真实办公画面上传到公共仓库。
- 不要提交 `.venv/`、`.qt_packages/`、模型权重、日志或个人配置。
- 项目只在本地枚举窗口并激活窗口，不上传摄像头画面或检测结果。
- 如果在公司设备上使用，请遵守公司关于摄像头、监控和信息安全的规定。

## 已知限制

- 目前只支持 Windows。
- PySide6 红框提醒适合当前桌面工具，但不同 Windows 缩放比例、多显示器布局下仍应实机确认边框位置。
- 多个同类窗口同时存在且目标窗口关闭重开时，需要重新选择目标。
- 摄像头角度、光线、遮挡和人物距离会影响检测效果。
- YOLO 模型越小，速度通常越快，但远距离人物和遮挡人物可能更容易漏检。
- 这是“人物经过”检测，不是严格的人脸识别或视线识别。
- 直接运行 `detect_camera.py` 是调试方式，可能会显示控制台；日常使用请运行 `app.py`。

## 开发建议

如果要继续提高稳定性，建议按以下顺序迭代：

1. 收集不同光线、不同经过方向和不同距离的视频片段。
2. 统计误触发和漏触发，而不是只看单次效果。
3. 优先调整背景区域、摄像头使用者排除规则和连续帧阈值。
4. 只有预训练模型在目标环境中持续漏检时，再考虑制作专用标注数据并微调 YOLO。
5. 增加真实摄像头的长时间回归测试。
