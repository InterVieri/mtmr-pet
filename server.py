#!/usr/bin/env python3
"""
MTMR 角色 widget 调试器 - 完整版
所有参数都在 GUI 上调
"""
import json
import re
import shutil
import subprocess
import tempfile
import threading
import time
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent            # 相对脚本自身，二开者放任何目录都自动正确
ITEMS_JSON = Path.home() / "Library/Application Support/MTMR/items.json"
WALK_DIR = PROJECT_DIR / "icons/walk"
BACK_DIR = PROJECT_DIR / "icons/back"
CHARS_DIR = PROJECT_DIR / "icons/chars"                  # 拖 GIF 进这里自动生效
STEP_SH = Path("/tmp/mtmr-step.sh")
WIDGET_LOG = Path("/tmp/mtmr-widgets.log")
PARAMS_FILE = PROJECT_DIR / "debug-params.json"
PARAMS_SH = PROJECT_DIR / "scripts/params.sh"
DIALOGS_FILE = PROJECT_DIR / "dialogs.txt"
CONFIG_FILE = PROJECT_DIR / "config.json"                # 部署配置（模型/字体/skill 等，调试器可编辑）
WATCH_LOG = Path("/tmp/mtmr-watch.log")                  # 自动监听日志
PORT = 8765
RENDER_SCALE = 2   # TouchBar @2x Retina：合成图按物理像素出（逻辑点 ×2 喂满物理像素，修历史模糊）
DIALOG_QUEUE = Path("/tmp/mtmr-dialog-queue.txt")   # 动态对话队列（生成器写，step.sh 消费）

DEFAULT_PARAMS = {
    "char_size": 26,
    "x_start": 20,
    "x_end": -1,  # -1 = auto（width - char - 20）
    "num_frames": 41,
    "background": "",  # 默认背景图名（空=back/ 目录第一张，避免硬编码私人素材）
    "char_selected": "char01.png",
    "pause_pct": 12,
    "reverse_pct": 8,
    "bubble_min": 30,
    "bubble_max": 60,
    "bubble_hold": 5,
    # continue_pct = 100 - pause - reverse
    "char_frames": 1,   # 角色动作帧数（rebuild 时按所选 GIF 实际帧数覆盖；1=静态）
    "render_scale": 2,  # TouchBar @2x：合成图物理像素倍数（逻辑点 × render_scale）
    "bubble_w_pt": 180,        # 气泡宽（逻辑点，@2x 后容 ~20 字）
    "bubble_font_pt": 8,       # 气泡字号（逻辑点）
    "dialog_mode": "dynamic",  # 对话来源：dynamic（skill 生成）/ static（dialogs.txt）
    "dialog_gen_interval": 120,  # 动态对话生成间隔（秒，每批生成 3 句）
}

# 部署配置（一次性，决定用哪个模型/字体/skill；和运行参数分开，调试器「部署配置」区编辑）
DEFAULT_CONFIG = {
    "claude_cmd": "claude",          # 大模型命令（print 模式）
    "dialog_skill": "",              # 对话 skill 名（如 /relationship-xiaobailong；空=不带 skill，走 prompt）
    "dialog_prompt": "像微信随口发3句不同的日常短句，口语自然，每句一行，只输出这3句，不要编号不要引号不要解释",
    "font": "/System/Library/Fonts/STHeiti Medium.ttc",   # 气泡字体
    "default_background": "",        # 默认背景图名（空=back/ 第一张）
}


def load_config():
    """读部署配置 config.json，缺键补默认"""
    cfg = dict(DEFAULT_CONFIG)
    if CONFIG_FILE.exists():
        try:
            cfg.update(json.loads(CONFIG_FILE.read_text()))
        except Exception:
            pass
    return cfg


def save_config(cfg):
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2, ensure_ascii=False))


def write_params_sh(p):
    """把运行参数 + 部署字体写到 scripts/params.sh 给 step.sh 读"""
    font = load_config().get("font", "/System/Library/Fonts/STHeiti Medium.ttc")
    content = f"""# MTMR 角色可调参数（调试器读写）
PAUSE_PCT={p.get('pause_pct', 12)}
REVERSE_PCT={p.get('reverse_pct', 8)}
BUBBLE_MIN_SEC={p.get('bubble_min', 30)}
BUBBLE_MAX_SEC={p.get('bubble_max', 60)}
BUBBLE_HOLD_SEC={p.get('bubble_hold', 5)}
CHAR_SIZE={p.get('char_size', 28)}          # 角色像素尺寸（compose_bubble 选帧 + 定位用）
CHAR_FRAMES={p.get('char_frames', 1)}       # 角色动作帧数（GIF 拆出，step.sh 按 pos%CHAR_FRAMES 轮换）
RENDER_SCALE={p.get('render_scale', 2)}     # TouchBar @2x：合成图物理像素倍数（逻辑点 × RENDER_SCALE）
BUBBLE_W_PT={p.get('bubble_w_pt', 180)}     # 气泡宽（逻辑点）
BUBBLE_FONT_PT={p.get('bubble_font_pt', 8)} # 气泡字号（逻辑点）
DIALOG_MODE={p.get('dialog_mode', 'dynamic')}  # 对话来源：dynamic / static
DIALOG_GEN_INTERVAL={p.get('dialog_gen_interval', 120)}  # 动态对话生成间隔（秒）
FONT={font}                                # 气泡字体（部署配置）
"""
    PARAMS_SH.write_text(content)


def read_dialogs():
    if DIALOGS_FILE.exists():
        return DIALOGS_FILE.read_text()
    return ""


def load_params():
    if PARAMS_FILE.exists():
        return json.loads(PARAMS_FILE.read_text())
    return dict(DEFAULT_PARAMS)


def save_params(p):
    PARAMS_FILE.write_text(json.dumps(p, indent=2))


def read_items():
    text = ITEMS_JSON.read_text()
    return json.loads(re.sub(r"//.*", "", text))


def write_items(data):
    ITEMS_JSON.write_text(json.dumps(data, indent=2, ensure_ascii=False))


def find_widget(data, widget_type):
    for item in data:
        if item.get("type") == widget_type:
            return item
    return None


def split_char_frames(source, size):
    """把 source（GIF 或单帧图）拆成 size×size 动作帧 PNG，返回生成路径列表。
    GIF：-coalesce 拆全部帧；PNG/单帧：作为 1 帧。
    -coalesce 是关键：把每帧合成到完整画布、消除 GIF 帧间 offset，否则单帧几何异常
    （历史踩坑：未 coalesce 直接 resize 得到 15×28 畸形窄条）。"""
    tmp = Path(tempfile.mkdtemp(prefix="mtmr-split-"))
    try:
        if source.suffix.lower() == ".gif":
            subprocess.run(["convert", str(source), "-coalesce", str(tmp / "f.png")],
                           capture_output=True)
            frames_in = sorted(tmp.glob("f-*.png"))
            if not frames_in and (tmp / "f.png").exists():   # 单帧 GIF 直接输出 f.png
                frames_in = [tmp / "f.png"]
        else:
            frames_in = [source]

        out = []
        for idx, fin in enumerate(frames_in):
            fout = WALK_DIR / f"char_{size}x{size}-{idx}.png"
            subprocess.run(["convert", str(fin), "-resize", f"{size}x{size}",
                            "-gravity", "center", "-background", "none",
                            "-extent", f"{size}x{size}", str(fout)], capture_output=True)
            out.append(fout)
        # 向后兼容：保留一张无后缀副本（= 第一帧），供旧引用 / compose_bubble fallback
        if out:
            shutil.copyfile(out[0], WALK_DIR / f"char_{size}x{size}.png")
        return out or [WALK_DIR / f"char_{size}x{size}.png"]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# rebuild + items.json 写回串行化：HTTP handler 与目录监听线程都可能触发，加锁防并发写
_REBUILD_LOCK = threading.Lock()


def rebuild_frames(width, params):
    """根据 params 重做帧。返回角色动作帧数 N（GIF 实际帧数）。
    所有合成图按 RENDER_SCALE ×物理像素出，喂满 TouchBar @2x 物理像素（修历史模糊）。"""
    char_size = params["char_size"]
    num = params["num_frames"]
    bg_name = params.get("background", "") or load_config().get("default_background", "")
    x_start = params["x_start"]
    x_end = params["x_end"] if params["x_end"] > 0 else (width - char_size - 20)
    S = RENDER_SCALE
    char_px = char_size * S          # 角色物理像素（28→56）
    px_w = width * S                 # 画布物理宽（400→800）
    px_h = 30 * S                    # 画布物理高（30→60）

    # 清理旧 walk 帧和当前物理尺寸的旧角色动作帧，避免上一轮残留帧干扰轮换
    for f in WALK_DIR.glob("walk_*.png"):
        f.unlink()
    for f in WALK_DIR.glob(f"char_{char_px}x{char_px}-*.png"):
        f.unlink()

    background = WALK_DIR / f"background_{px_w}x{px_h}.png"
    source_back = BACK_DIR / bg_name if bg_name else None
    if not (source_back and source_back.is_file()):
        source_back = None
        if BACK_DIR.exists():                       # fallback：back/ 目录第一张
            for f in BACK_DIR.iterdir():
                if f.is_file():
                    source_back = f
                    break
    source_char = CHARS_DIR / params.get("char_selected", "char01.png")
    if not source_char.exists():
        source_char = PROJECT_DIR / "icons/frames/frame00.png"   # fallback（相对项目）

    # 背景：按物理像素铺满；back/ 无图时纯色兜底（分享项目不含素材也能跑）
    if source_back:
        subprocess.run(["convert", str(source_back), "-resize", f"{px_w}x{px_h}^",
                        "-gravity", "center", "-extent", f"{px_w}x{px_h}", str(background)],
                       capture_output=True)
    else:
        subprocess.run(["convert", "-size", f"{px_w}x{px_h}", "xc:#222233", str(background)],
                       capture_output=True)

    # 动态拆角色动作帧（按物理像素 char_px；GIF 多帧 / 单帧图 1 帧）
    char_frames = split_char_frames(source_char, char_px)
    n_frames = len(char_frames)

    # rebuild 是唯一知道实际帧数的地方，写回 params + params.sh 供 step.sh 读
    params["char_frames"] = n_frames
    save_params(params)
    write_params_sh(params)

    y = (30 - char_size) // 2 * S    # 角色竖直物理偏移（逻辑 ((30-char_size)/2) × S）
    for i in range(num):
        if num == 1:
            x = x_start
        else:
            x = x_start + (x_end - x_start) * i // (num - 1)
        # 动作相位随位置循环：walk_01 用帧 0、walk_(N+1) 又回帧 0…
        # 停顿时 step.sh 返回同一 walkXX，动作也停 —— 符合真实走路直觉
        char = char_frames[i % n_frames]
        # 用 -page 精确定位（跟 step.sh compose_bubble 一致，均按物理像素）
        subprocess.run(["convert", "-size", f"{px_w}x{px_h}", "xc:none",
                        "(", str(background), "-repage", "+0+0", ")",
                        "(", str(char), "-repage", f"+{x*S}+{y}", ")",
                        "-flatten", str(WALK_DIR / f"walk_{i+1:02d}.png")],
                       capture_output=True)
    return n_frames


def rebuild_and_commit(width, params):
    """rebuild 帧并写回 items.json 的 alternativeImages/image；返回动作帧数。
    HTTP handler 与目录监听器共用，加锁避免并发写 items.json。"""
    with _REBUILD_LOCK:
        n = rebuild_frames(width, params)
        data = read_items()
        widget = find_widget(data, "appleScriptTitledButton")
        if widget:
            num = params["num_frames"]
            alt = {f"walk{i:02d}": {"filePath": str(WALK_DIR / f"walk_{i:02d}.png")}
                   for i in range(1, num + 1)}
            alt["bubble"] = {"filePath": str(WALK_DIR / "bubble_current.png")}
            widget['alternativeImages'] = alt
            widget['image'] = {"filePath": str(WALK_DIR / "walk_01.png")}
            write_items(data)
        return n


def update_step_sh(params):
    """更新 bash 状态机"""
    pause = params["pause_pct"]
    reverse = params["reverse_pct"]
    pause_end = pause
    reverse_end = pause + reverse
    max_pos = params["num_frames"] - 1
    init_pos = max_pos // 2
    content = f"""#!/bin/bash
# 调试器生成：{pause}% 停 / {reverse}% 反向 / {100-pause-reverse}% 继续
state=$(cat /tmp/mtmr-state 2>/dev/null || echo "{init_pos} right")
pos=$(echo "$state" | cut -d' ' -f1)
dir=$(echo "$state" | cut -d' ' -f2)

rnd=$((RANDOM % 100))
if [ "$rnd" -lt {pause_end} ]; then
    new_pos=$pos
elif [ "$rnd" -lt {reverse_end} ]; then
    if [ "$dir" = "right" ]; then
        dir="left"; new_pos=$((pos - 1))
    else
        dir="right"; new_pos=$((pos + 1))
    fi
else
    if [ "$dir" = "right" ]; then
        new_pos=$((pos + 1))
    else
        new_pos=$((pos - 1))
    fi
fi

if [ "$new_pos" -ge {max_pos} ]; then new_pos={max_pos}; dir="left"; fi
if [ "$new_pos" -le 0 ]; then new_pos=0; dir="right"; fi

echo "$new_pos $dir" > /tmp/mtmr-state
echo "$new_pos"
"""
    STEP_SH.write_text(content)
    STEP_SH.chmod(0o755)


def restart_mtmr():
    WIDGET_LOG.unlink(missing_ok=True)
    Path("/tmp/mtmr-state").unlink(missing_ok=True)
    subprocess.run(["killall", "MTMR"], capture_output=True)
    time.sleep(1)
    subprocess.run(["open", "-a", "MTMR"])
    time.sleep(4)


def get_status():
    data = read_items()
    widget = find_widget(data, "appleScriptTitledButton")
    others = []
    for item in data:
        if item.get("type") != "appleScriptTitledButton":
            others.append({"type": item.get("type"), "align": item.get("align", "?"), "width": item.get("width", "?")})
    backs = [f.name for f in BACK_DIR.iterdir() if f.is_file()] if BACK_DIR.exists() else []
    chars = sorted([f.name for f in CHARS_DIR.iterdir() if f.is_file()]) if CHARS_DIR.exists() else []
    return {
        "widget": {
            "width": widget.get("width") if widget else None,
            "align": widget.get("align") if widget else None,
            "refreshInterval": widget.get("refreshInterval") if widget else None,
        },
        "others": others,
        "params": load_params(),
        "backs": backs,
        "chars": chars,
        "dialogs": read_dialogs(),
        "positions": read_positions(),
        "config": load_config()
    }


def read_positions():
    if not WIDGET_LOG.exists():
        return []
    seen = set()
    result = []
    for line in WIDGET_LOG.read_text().strip().split("\n"):
        if line and line not in seen:
            seen.add(line)
            result.append(line)
    return result


def watch_log(msg):
    """自动监听日志：同时打到 stdout 和 /tmp/mtmr-watch.log（调试器面板/终端可读）"""
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    try:
        with WATCH_LOG.open("a") as f:
            f.write(line + "\n")
    except OSError:
        pass


def watch_chars_dir():
    """后台轮询 icons/chars/*.gif：检测到新增/变化的 GIF 自动接入。
    流程：设为 char_selected → rebuild_and_commit → 重启 MTMR。
    纯标准库（无 watchdog 依赖）；chars 目录小，1.5s 轮询成本可忽略。"""
    if not CHARS_DIR.exists():
        watch_log(f"监听目录不存在：{CHARS_DIR}，自动生效未启动")
        return
    # 基线快照：启动时不把已有 GIF 当新增
    snapshot = {}
    for f in CHARS_DIR.glob("*.gif"):
        try:
            snapshot[f.name] = f.stat().st_mtime
        except OSError:
            pass
    watch_log(f"已启动 chars/ 监听（{len(snapshot)} 个现有 GIF 为基线）：拖入 GIF 即自动生效")

    while True:
        time.sleep(1.5)
        current = {}
        for f in CHARS_DIR.glob("*.gif"):
            try:
                current[f.name] = f.stat().st_mtime
            except OSError:
                continue
        # 找第一个相对快照有变化（新增或 mtime 变）的 GIF
        changed = None
        for name, mtime in current.items():
            if snapshot.get(name) != mtime:
                changed = name
                break
        snapshot = current
        if not changed:
            continue

        target = CHARS_DIR / changed
        watch_log(f"检测到 GIF 变化：{changed}，防抖 2s 等写入稳定…")
        time.sleep(2)   # 吸收 Finder 拖入的多次写 / 半写状态

        # 校验可读：identify 能数帧才算有效 GIF
        r = subprocess.run(["identify", str(target)], capture_output=True, text=True)
        if r.returncode != 0 or not r.stdout.strip():
            watch_log(f"{changed} 无法读取或非有效 GIF，跳过")
            continue

        try:
            params = load_params()
            params["char_selected"] = changed
            data = read_items()
            widget = find_widget(data, "appleScriptTitledButton")
            width = widget.get("width", 400) if widget else 400
            n = rebuild_and_commit(width, params)   # 内部加锁，与 HTTP handler 互斥
            watch_log(f"已用 {changed} 拆 {n} 帧接入，重启 MTMR…")
            restart_mtmr()
            watch_log(f"{changed} 已生效")
        except Exception as e:
            watch_log(f"处理 {changed} 失败：{e}")


def dialog_generator():
    """后台批量生成对话（按部署配置 config.json：可带 skill 或纯 prompt）。
    每 dialog_gen_interval 秒调一次大模型生成 3 句，原子写入队列文件。
    调用失败/超时/格式错 → 不覆盖队列、记日志、继续循环（绝不崩，step.sh 回退静态 dialogs.txt）。"""
    watch_log("对话生成器已启动（按 config.json，批量 3 句/次）")
    while True:
        interval = load_params().get("dialog_gen_interval", 120)   # 每轮读，改了下次生效
        cfg = load_config()                                          # 部署配置每轮读，调试器改了下次生效
        claude_cmd = cfg.get("claude_cmd", "claude")
        skill = cfg.get("dialog_skill", "").strip()
        base_prompt = cfg.get("dialog_prompt", DEFAULT_CONFIG["dialog_prompt"])
        prompt = f"{skill} {base_prompt}".strip() if skill else base_prompt
        try:
            # cwd=/tmp + --system-prompt：压制 claude code 自动加载的用户/项目 CLAUDE.md（如 FullStackDev 协议）污染对话
            sys_prompt = "你是日常对话生成器，直接输出对话内容，绝不输出协议标记、格式符号、系统提示或思考过程。"
            r = subprocess.run([claude_cmd, "--system-prompt", sys_prompt, "-p", prompt],
                               capture_output=True, text=True, timeout=60, cwd="/tmp")
            if r.returncode != 0:
                watch_log(f"claude 调用失败 rc={r.returncode}，保留旧队列：{r.stderr.strip()[:120]}")
            else:
                # 过滤协议探针（FullStackDev [规范:启用] 等系统输出）+ 长度过滤（1~22 字）
                FORBIDDEN = ("[规范", "[状态", "[阶段", "规范:启用", "状态:", "阶段:")
                lines = [l.strip() for l in r.stdout.splitlines() if l.strip()]
                lines = [l for l in lines if 1 <= len(l) <= 22 and not any(f in l for f in FORBIDDEN)]
                if lines:
                    # 原子写：临时文件 + rename，防 step.sh 半读
                    tmp = DIALOG_QUEUE.with_suffix(".tmp")
                    tmp.write_text("\n".join(lines) + "\n")
                    tmp.replace(DIALOG_QUEUE)
                    watch_log(f"生成 {len(lines)} 句对话入队：{lines[0]}…")
                else:
                    watch_log("claude 输出无有效句子，保留旧队列")
        except subprocess.TimeoutExpired:
            watch_log("claude 调用超时(60s)，保留旧队列")
        except FileNotFoundError:
            watch_log("claude 命令不存在，生成器无法工作（可切静态模式）")
        except Exception as e:
            watch_log(f"生成器异常（不崩，继续）：{e}")
        time.sleep(interval)


HTML = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>MTMR 调试器</title>
<style>
body { font-family: -apple-system, sans-serif; max-width: 1100px; margin: 20px auto; padding: 20px; background: #f5f5f7; }
h1 { color: #1d1d1f; }
h2 { color: #1d1d1f; border-bottom: 1px solid #d2d2d7; padding-bottom: 8px; margin-top: 0; }
.section { background: white; padding: 16px 20px; margin: 12px 0; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }
button { background: #0071e3; color: white; border: none; padding: 8px 16px; border-radius: 6px; cursor: pointer; font-size: 14px; margin: 4px 2px; }
button:hover { background: #0077ed; }
button.success { background: #34c759; }
button.warn { background: #ff9500; }
input, select { padding: 6px 10px; font-size: 14px; border: 1px solid #d2d2d7; border-radius: 6px; margin: 4px 2px; }
pre { background: #1d1d1f; color: #f5f5f7; padding: 12px; border-radius: 6px; overflow-x: auto; font-size: 12px; }
.row { display: flex; align-items: center; gap: 8px; margin: 6px 0; flex-wrap: wrap; }
.bg-blue { background: #e3f0ff; padding: 4px 8px; border-radius: 4px; display: inline-block; margin: 2px; font-size: 13px; }
.status { font-family: monospace; font-size: 13px; }
.label { min-width: 140px; color: #424245; }
</style></head>
<body>
<h1>MTMR 角色 widget 调试器</h1>

<div class="section">
  <h2>1. 角色 widget（items.json 直接改）</h2>
  <div class="row">
    <span class="label">width:</span>
    <input id="width" type="number" style="width:100px">
    <button onclick="setWidget('width')">应用</button>
  </div>
  <div class="row">
    <span class="label">align:</span>
    <button onclick="setWidget('align','left')">left</button>
    <button onclick="setWidget('align','center')">center</button>
    <button onclick="setWidget('align','right')">right</button>
  </div>
  <div class="row">
    <span class="label">refreshInterval (秒):</span>
    <input id="refresh" type="number" step="0.05" style="width:100px">
    <button onclick="setWidget('refreshInterval')">应用</button>
    <span style="color:#6e6e73;font-size:12px">(0.1=快, 0.5=慢)</span>
  </div>
</div>

<div class="section">
  <h2>2. 角色（重做帧）</h2>
  <div class="row">
    <span class="label">角色 size (像素):</span>
    <input id="char_size" type="number" style="width:80px" min="10" max="28">
    <span style="color:#6e6e73;font-size:12px">(touchbar 高 30，最大 28)</span>
  </div>
  <div class="row">
    <span class="label">x 起始:</span>
    <input id="x_start" type="number" style="width:80px">
    <span class="label" style="margin-left:16px">x 终止:</span>
    <input id="x_end" type="number" style="width:80px">
    <span style="color:#6e6e73;font-size:12px">(-1 = 自动到右边)</span>
  </div>
  <div class="row">
    <span class="label">帧数:</span>
    <input id="num_frames" type="number" style="width:80px" min="2" max="100">
    <span style="color:#6e6e73;font-size:12px">(越多越顺滑)</span>
  </div>
  <div class="row">
    <span class="label">背景图:</span>
    <select id="background" style="min-width:400px"></select>
  </div>
  <div class="row">
    <span class="label">角色（11 个 GIF 选一）:</span>
    <select id="char_selected" style="min-width:200px"></select>
  </div>
  <div class="row">
    <button class="warn" onclick="rebuildFrames()">重做帧（用上面参数）</button>
    <span style="color:#6e6e73;font-size:12px">⚠️ 这会覆盖现有 scene_*.png</span>
  </div>
</div>

<div class="section">
  <h2>3. 走路行为</h2>
  <div class="row">
    <span class="label">停顿概率 %:</span>
    <input id="pause_pct" type="number" style="width:80px" min="0" max="100">
    <span class="label" style="margin-left:16px">反向概率 %:</span>
    <input id="reverse_pct" type="number" style="width:80px" min="0" max="100">
    <span style="color:#6e6e73;font-size:12px">继续 = 100 - 停 - 反</span>
  </div>
  <div class="row">
    <button class="warn" onclick="updateParams()">更新参数（立即生效）</button>
  </div>
</div>

<div class="section">
  <h2>4. 对话浮现</h2>
  <div class="row">
    <span class="label">对话来源:</span>
    <button onclick="setDialogMode('dynamic')">动态（AI 生成）</button>
    <button onclick="setDialogMode('static')">静态（dialogs.txt）</button>
    <span id="dialog_mode_now" style="color:#6e6e73;font-size:12px"></span>
  </div>
  <div class="row">
    <span class="label">浮现间隔(秒):</span>
    <input id="bubble_min" type="number" style="width:70px" min="1"> 到
    <input id="bubble_max" type="number" style="width:70px" min="1">
    <span class="label" style="margin-left:16px">停留(秒):</span>
    <input id="bubble_hold" type="number" style="width:70px" min="1">
  </div>
  <div class="row">
    <button class="warn" onclick="updateParams()">更新参数（立即生效）</button>
  </div>
  <div class="row" style="flex-direction:column; align-items:flex-start">
    <span class="label">对话内容（每行一句，随机选）:</span>
    <textarea id="dialogs" style="width:400px; height:120px; font-size:14px; padding:8px; border:1px solid #d2d2d7; border-radius:6px; margin-top:6px"></textarea>
  </div>
  <div class="row">
    <button class="warn" onclick="saveDialogs()">保存对话（立即生效）</button>
  </div>
</div>

<div class="section">
  <h2>4. 其他 widget 对齐</h2>
  <div class="row">
    <button onclick="setOthersAlign('left')">全部 left</button>
    <button onclick="setOthersAlign('center')">全部 center</button>
    <button onclick="setOthersAlign('right')">全部 right</button>
  </div>
</div>

<div class="section">
  <h2>部署配置（一次性：模型 / 字体 / skill，保存即生效）</h2>
  <div class="row">
    <span class="label">大模型命令:</span>
    <input id="cfg_claude_cmd" type="text" style="width:300px">
  </div>
  <div class="row">
    <span class="label">对话 skill:</span>
    <input id="cfg_dialog_skill" type="text" style="width:300px" placeholder="如 /relationship-xiaobailong（空=不带 skill）">
  </div>
  <div class="row" style="flex-direction:column;align-items:flex-start">
    <span class="label">对话 prompt 模板:</span>
    <textarea id="cfg_dialog_prompt" style="width:600px;height:60px"></textarea>
  </div>
  <div class="row">
    <span class="label">气泡字体:</span>
    <input id="cfg_font" type="text" style="width:400px">
  </div>
  <div class="row">
    <span class="label">默认背景图:</span>
    <input id="cfg_default_background" type="text" style="width:300px" placeholder="空=back/ 第一张">
  </div>
  <div class="row">
    <button class="warn" onclick="saveConfig()">保存部署配置（生成器下轮 + 字体立即生效）</button>
  </div>
</div>

<div class="section">
  <h2>5. 操作</h2>
  <div class="row">
    <button class="success" onclick="apply()">应用 + 重启 MTMR</button>
    <button onclick="refreshStatus()">刷新状态</button>
  </div>
</div>

<div class="section">
  <h2>当前状态 + widget 实际位置</h2>
  <div id="status" class="status">加载中...</div>
</div>

<script>
function api(path, body) {
  return fetch('/api/' + path, {
    method: body ? 'POST' : 'GET',
    headers: {'Content-Type': 'application/json'},
    body: body ? JSON.stringify(body) : null
  }).then(r => r.json());
}

function setWidget(field, value) {
  if (value === undefined) {
    value = document.getElementById(field === 'refreshInterval' ? 'refresh' : field).value;
  }
  api('set_widget', {field, value}).then(refreshStatus);
}

function setOthersAlign(align) {
  api('set_others_align', {align}).then(refreshStatus);
}

function getParams() {
  return {
    char_size: parseInt(document.getElementById('char_size').value),
    x_start: parseInt(document.getElementById('x_start').value),
    x_end: parseInt(document.getElementById('x_end').value),
    num_frames: parseInt(document.getElementById('num_frames').value),
    background: document.getElementById('background').value,
    char_selected: document.getElementById('char_selected').value,
    pause_pct: parseInt(document.getElementById('pause_pct').value),
    reverse_pct: parseInt(document.getElementById('reverse_pct').value),
    bubble_min: parseInt(document.getElementById('bubble_min').value),
    bubble_max: parseInt(document.getElementById('bubble_max').value),
    bubble_hold: parseInt(document.getElementById('bubble_hold').value),
  };
}

function rebuildFrames() {
  api('rebuild_frames', getParams()).then(r => {
    alert(r.ok ? '帧已重做，记得按"应用+重启"' : '失败：' + r.error);
    refreshStatus();
  });
}

function updateParams() {
  api('update_params', getParams()).then(r => {
    alert(r.ok ? '参数已更新（立即生效，无需重启）' : '失败');
    refreshStatus();
  });
}

function saveDialogs() {
  const text = document.getElementById('dialogs').value;
  api('save_dialogs', {dialogs: text}).then(r => {
    alert(r.ok ? '对话已保存（立即生效）' : '失败');
    refreshStatus();
  });
}

function setDialogMode(mode) {
  api('set_dialog_mode', {mode}).then(r => {
    document.getElementById('dialog_mode_now').textContent = '已切换：' + (r.mode === 'dynamic' ? '动态' : '静态');
    refreshStatus();
  });
}

function saveConfig() {
  const cfg = {
    claude_cmd: document.getElementById('cfg_claude_cmd').value,
    dialog_skill: document.getElementById('cfg_dialog_skill').value,
    dialog_prompt: document.getElementById('cfg_dialog_prompt').value,
    font: document.getElementById('cfg_font').value,
    default_background: document.getElementById('cfg_default_background').value,
  };
  api('save_config', cfg).then(r => { alert(r.ok ? '部署配置已保存（生成器下轮 + 字体立即生效）' : '失败'); refreshStatus(); });
}

function apply() {
  api('apply', {}).then(refreshStatus);
}

function refreshStatus() {
  api('status').then(s => {
    document.getElementById('width').value = s.widget.width || '';
    document.getElementById('refresh').value = s.widget.refreshInterval || '';
    document.getElementById('char_size').value = s.params.char_size;
    document.getElementById('x_start').value = s.params.x_start;
    document.getElementById('x_end').value = s.params.x_end;
    document.getElementById('num_frames').value = s.params.num_frames;
    document.getElementById('pause_pct').value = s.params.pause_pct;
    document.getElementById('reverse_pct').value = s.params.reverse_pct;
    document.getElementById('bubble_min').value = s.params.bubble_min;
    document.getElementById('bubble_max').value = s.params.bubble_max;
    document.getElementById('bubble_hold').value = s.params.bubble_hold;
    if (document.activeElement.id !== 'dialogs') {
      document.getElementById('dialogs').value = s.dialogs || '';
    }
    // 背景下拉
    const sel = document.getElementById('background');
    sel.innerHTML = '';
    for (const b of s.backs) {
      const opt = document.createElement('option');
      opt.value = b; opt.textContent = b;
      if (b === s.params.background) opt.selected = true;
      sel.appendChild(opt);
    }
    // 角色下拉
    const selChar = document.getElementById('char_selected');
    selChar.innerHTML = '';
    for (const c of s.chars) {
      const opt = document.createElement('option');
      opt.value = c; opt.textContent = c;
      if (c === s.params.char_selected) opt.selected = true;
      selChar.appendChild(opt);
    }
    let html = '<div><b>角色 widget:</b> width=' + s.widget.width + ' align=' + s.widget.align + ' refresh=' + s.widget.refreshInterval + '</div>';
    html += '<div style="margin-top:6px"><b>角色参数:</b> size=' + s.params.char_size + ' x=[' + s.params.x_start + ',' + s.params.x_end + '] frames=' + s.params.num_frames + '</div>';
    html += '<div style="margin-top:6px"><b>bash:</b> 停=' + s.params.pause_pct + '% 反=' + s.params.reverse_pct + '% 续=' + (100-s.params.pause_pct-s.params.reverse_pct) + '%</div>';
    html += '<div style="margin-top:6px"><b>背景:</b> ' + s.params.background + '</div>';
    html += '<div style="margin-top:8px"><b>其他 widget (' + s.others.length + '):</b></div>';
    for (const o of s.others) {
      html += '<span class="bg-blue">' + o.type + ' (align=' + o.align + ', w=' + o.width + ')</span>';
    }
    html += '<div style="margin-top:10px"><b>widget 实际位置（winX=横向起点, w=宽度, h=高度）:</b></div>';
    html += '<pre>' + (s.positions.join('\\n') || '(没数据，按"应用+重启"') + '</pre>';
    const dm = document.getElementById('dialog_mode_now');
    if (dm) dm.textContent = s.params.dialog_mode === 'dynamic' ? '当前：动态（AI 生成）' : '当前：静态（dialogs.txt）';
    if (s.config) {
      document.getElementById('cfg_claude_cmd').value = s.config.claude_cmd || '';
      document.getElementById('cfg_dialog_skill').value = s.config.dialog_skill || '';
      document.getElementById('cfg_dialog_prompt').value = s.config.dialog_prompt || '';
      document.getElementById('cfg_font').value = s.config.font || '';
      document.getElementById('cfg_default_background').value = s.config.default_background || '';
    }
    document.getElementById('status').innerHTML = html;
  });
}

refreshStatus();
</script>
</body></html>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.path == '/':
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.end_headers()
            self.wfile.write(HTML.encode('utf-8'))
        elif self.path == '/api/status':
            self.send_json(get_status())
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        length = int(self.headers.get('Content-Length', 0))
        body = json.loads(self.rfile.read(length) or '{}')
        try:
            if self.path == '/api/set_widget':
                data = read_items()
                widget = find_widget(data, "appleScriptTitledButton")
                if widget:
                    value = body['value']
                    if body['field'] in ('width', 'refreshInterval'):
                        value = float(value) if body['field'] == 'refreshInterval' else int(value)
                        widget[body['field']] = value
                    elif body['field'] == 'align':
                        widget['align'] = value
                    write_items(data)
                self.send_json({'ok': True})

            elif self.path == '/api/set_others_align':
                data = read_items()
                for item in data:
                    if item.get('type') != 'appleScriptTitledButton':
                        item['align'] = body['align']
                write_items(data)
                self.send_json({'ok': True})

            elif self.path == '/api/rebuild_frames':
                params = load_params()
                params.update({k: body[k] for k in ['char_size', 'x_start', 'x_end', 'num_frames', 'background', 'char_selected'] if k in body})
                data = read_items()
                widget = find_widget(data, "appleScriptTitledButton")
                width = widget.get('width', 400) if widget else 400
                n = rebuild_and_commit(width, params)
                self.send_json({'ok': True, 'char_frames': n})

            elif self.path == '/api/update_params':
                params = load_params()
                params.update({k: body[k] for k in ['pause_pct', 'reverse_pct', 'bubble_min', 'bubble_max', 'bubble_hold'] if k in body})
                save_params(params)
                write_params_sh(params)   # 写 scripts/params.sh 给 step.sh 读
                self.send_json({'ok': True})

            elif self.path == '/api/save_dialogs':
                DIALOGS_FILE.write_text(body.get('dialogs', ''))
                self.send_json({'ok': True})

            elif self.path == '/api/set_dialog_mode':
                mode = body.get('mode', 'dynamic')
                if mode not in ('dynamic', 'static'):
                    mode = 'dynamic'
                params = load_params()
                params['dialog_mode'] = mode
                save_params(params)
                write_params_sh(params)   # 立即写 params.sh，step.sh 下次刷新（0.25s）读到
                self.send_json({'ok': True, 'mode': mode})

            elif self.path == '/api/save_config':
                cfg = load_config()
                cfg.update({k: body[k] for k in ['claude_cmd', 'dialog_skill', 'dialog_prompt', 'font', 'default_background'] if k in body})
                save_config(cfg)
                write_params_sh(load_params())   # font 改了，重写 params.sh 让 step.sh 下次读到
                self.send_json({'ok': True})

            elif self.path == '/api/apply':
                restart_mtmr()
                self.send_json({'ok': True})

            else:
                self.send_response(404)
                self.end_headers()
        except Exception as e:
            self.send_json({'ok': False, 'error': str(e)})

    def send_json(self, obj):
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps(obj, ensure_ascii=False).encode('utf-8'))


if __name__ == '__main__':
    print(f"调试器运行在 http://localhost:{PORT}")
    print("按 Ctrl+C 退出")
    # 启动 chars/ 目录监听（daemon：随主进程退出）
    threading.Thread(target=watch_chars_dir, daemon=True).start()
    # 启动动态对话生成器（走 /relationship-xiaobailong skill，daemon）
    threading.Thread(target=dialog_generator, daemon=True).start()
    webbrowser.open(f'http://localhost:{PORT}')
    HTTPServer(('127.0.0.1', PORT), Handler).serve_forever()
