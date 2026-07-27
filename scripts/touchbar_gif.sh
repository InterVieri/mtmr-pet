#!/bin/bash
# 截 TouchBar 动图 GIF：定时触发 Cmd+Shift+6（TouchBar 截图）+ ImageMagick 合成 GIF
#
# 依赖：
#   1. TouchBar 截图快捷键已开（系统设置 → 键盘 → 快捷键 → 屏幕快照 → 勾 Touch Bar / Cmd+Shift+6）
#   2. 辅助功能权限（系统设置 → 隐私与安全性 → 辅助功能 → 给运行脚本的程序 Terminal/Claude 打勾）
#   3. ImageMagick（convert）
#
# 用法：bash scripts/touchbar_gif.sh [帧数] [间隔秒] [输出路径]
#   默认 40 帧 / 0.4s / /tmp/touchbar.gif

FRAMES="${1:-40}"
INTERVAL="${2:-0.4}"
OUT="${3:-/tmp/touchbar.gif}"
FRAME_DIR="/tmp/tb_frames"
# 截图存储位置（用户可能改过，如 ~/Documents/；动态读，默认桌面）
SCREEN_DIR=$(defaults read com.apple.screencapture location 2>/dev/null || echo "~/Desktop")
SCREEN_DIR="${SCREEN_DIR/#\~/$HOME}"

mkdir -p "$FRAME_DIR"
rm -f "$FRAME_DIR"/*.png

echo "截 $FRAMES 帧，每 ${INTERVAL}s（TouchBar 保持显示，期间别切走）..."
for i in $(seq 1 "$FRAMES"); do
    osascript -e 'tell application "System Events" to key code 22 using {command down, shift down}' 2>/dev/null
    sleep "$INTERVAL"
    latest=$(ls -t "$SCREEN_DIR"/"Touch Bar Shot"*.png "$SCREEN_DIR"/触控栏*.png 2>/dev/null | head -1)
    if [ -n "$latest" ]; then
        mv "$latest" "$FRAME_DIR/frame_$(printf '%03d' "$i").png"
        echo -n "."
    else
        echo -n "x"
    fi
done
echo ""

captured=$(ls "$FRAME_DIR"/frame_*.png 2>/dev/null | wc -l | tr -d ' ')
echo "截到 $captured / $FRAMES 帧"
if [ "$captured" -eq 0 ]; then
    echo "⚠️ 一帧没截到——osascript 没辅助功能权限。"
    echo "   去 系统设置 → 隐私与安全性 → 辅助功能，给运行脚本的程序（Terminal / Claude）打勾，再跑。"
    exit 1
fi

echo "合成 GIF → $OUT"
delay=$(awk -v iv="$INTERVAL" 'BEGIN{printf "%d", iv*100}')
convert -delay "$delay" -loop 0 "$FRAME_DIR"/frame_*.png "$OUT" 2>/dev/null
ls -lh "$OUT"
echo "完成: $OUT"
