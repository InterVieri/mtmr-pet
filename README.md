# MTMR TouchBar 角色

让一个卡通角色在你的 MacBook Pro TouchBar 上闲逛——随机方向走动、偶尔停顿转身，背景是像素场景。每隔 30-60 秒浮现一个对话框，对话内容可以**静态预设**或**大模型动态生成**（可注入任意人设 skill）。

基于 [Toxblh/MTMR](https://github.com/Toxblh/MTMR)（MIT）二次开发。

## 特性

- **@2x 高清渲染**：合成帧按 TouchBar Retina 物理像素出（800×60），喂满物理像素，告别模糊
- **任意 GIF 自动拆帧**：把任意 `.gif` 拖进 `icons/chars/`，自动拆全部帧，角色边走边动（动态帧数，不写死）
- **AI 动态对话**：对话框内容由大模型（claude code CLI）实时生成，支持注入任意人设 skill；静态/动态一键切换
- **容错**：大模型调不通自动回退静态对话，绝不崩
- **气泡宽度自适应**：气泡按文字实际渲染宽度动态变化，短句小气泡、长句大气泡
- **Web 调试器**：可视化调参（角色大小、走路行为、气泡、对话），改完即时生效
- **全配置化**：路径自动相对定位，部署配置（模型/字体/skill）和运行参数分离，二开者放任何目录都能跑

## 依赖

- 带 TouchBar 的 MacBook Pro（macOS 10.12.2+）
- **改版 MTMR.app**（见安装；官方版会把图缩到 24×24 导致模糊，必须用改版）
- ImageMagick 7（`convert` / `identify`）
- Python 3（标准库，无第三方依赖）
- （可选）[Claude Code](https://claude.com/claude-code) CLI —— 动态对话用；不装则只能用静态对话

## 安装

1. **装改版 MTMR.app**：从本仓库 Release 下载 `MTMR.app`，替换官方 MTMR.app。
   - 改版相对官方做了两处修复：图 resize 不放大（角色不被压扁）、maxSize 放开到 2000（@2x 大图不被缩成 24×24）。
   - 想自己编译：源码在本仓库 `MTMR/` 子目录（含上述改动 + 上游 MIT LICENSE），执行：
     ```
     cd MTMR
     xcodebuild -project MTMR.xcodeproj -scheme MTMR -configuration Release \
       CODE_SIGN_IDENTITY="-" CODE_SIGNING_REQUIRED=NO CODE_SIGNING_ALLOWED=NO build
     ```
     产物在 `MTMR/build/Build/Products/Release/MTMR.app`，复制到 `/Applications/` 即可。
2. **装 ImageMagick**：`brew install imagemagick`
3. **clone 本仓库**：放任何目录都行，路径自动相对。
4. **准备素材**：在 `icons/chars/` 放角色 GIF，`icons/back/` 放背景图。仓库不含素材。
5. **配置 items.json**：把仓库示例 `items.json` 的 widget 配到 `~/Library/Application Support/MTMR/items.json`，`source.inline` 指向本项目的 `scripts/step.sh`。
6. **启动调试器**：双击 `调试器.command`（或 `python3 server.py`），浏览器打开 http://localhost:8765。

## 配置（两层，都在调试器里管）

**部署配置** `config.json`（一次性，调试器「部署配置」区编辑）：
- `claude_cmd`：大模型命令（默认 `claude`）
- `dialog_skill`：对话 skill 名（如 `/your-relationship-skill`；空=不带 skill，走纯 prompt）
- `dialog_prompt`：对话 prompt 模板
- `font`：气泡字体
- `default_background`：默认背景图名（空=`icons/back/` 第一张）

**运行参数** `debug-params.json`（调试器面板调，即时生效）：
- 角色大小、走路停顿/反向概率、气泡浮现间隔/停留、对话来源（动态/静态）、@2x 倍数、气泡字号等

**静态对话** `dialogs.txt`：每行一句，静态模式随机选；动态模式作回退。

## 工作原理

- `scripts/step.sh`：MTMR 每次刷新（0.25s）调用，返回走路帧 / 对话帧 label，维护闲逛状态机。
- `server.py`：Web 调试器 + 两个后台线程：
  - `watch_chars_dir`：监听 `icons/chars/*.gif`，拖入新 GIF 自动拆帧 + 重做帧 + 重启 MTMR
  - `dialog_generator`：按 `config.json` 调大模型批量生成对话入队（容错，失败回退静态）
- 合成帧按 `RENDER_SCALE=2` 出物理像素，喂满 TouchBar @2x。

## 二开

- **换角色**：把你的 GIF 拖进 `icons/chars/`，自动生效。
- **换对话人设**：调试器「部署配置」填你的 skill 名（Claude Code 的 relationship/colleague skill），或改 `dialog_prompt`。
- **换模型**：改 `claude_cmd` 指向你的模型 CLI。
- **换背景/字体**：素材放 `icons/back/`，字体改 `config.json` 的 `font`。

## 素材目录

```
icons/
├── chars/    # 角色 GIF（拖进来自动拆帧）
├── back/     # 背景图
├── walk/     # 运行时合成的帧（自动生成，勿手改）
└── frames/   # GIF 拆出的帧备用
```

## 已知限制

- TouchBar widget 高度物理 30 点（@2x = 60 像素），无法突破（改了会让 dock 一起变高）。
- @2x 已是物理像素极限，无法更清晰。
- 动态对话走大模型订阅额度，吃紧可调大 `dialog_gen_interval`。

## 致谢

基于 [Toxblh/MTMR](https://github.com/Toxblh/MTMR)（MIT），Copyright (c) 2018 Anton Palgunov。

## 协议

MIT
