#!/bin/bash
# MTMR 角色闲逛 + 定时对话（动态 AI 生成 / 静态 dialogs.txt）
# 平时走路（无框 walkXX），每 30-60 秒浮现对话框，停 5 秒；浮现期间角色继续走，对话框跟着移动
PROJ="$(cd "$(dirname "$0")/.." && pwd)"   # 相对脚本自身定位项目根（二开者放任何目录都对）
WALK="$PROJ/icons/walk"
DIALOGS="$PROJ/dialogs.txt"
STATE="/tmp/mtmr-state"
BUBBLE_TEXT="/tmp/mtmr-bubble-text"
BUBBLE_IMG="$WALK/bubble_current.png"
FONT="/System/Library/Fonts/STHeiti Medium.ttc"   # 默认字体，params.sh 的 FONT 会覆盖

# 读可调参数（调试器写入）
PAUSE_PCT=12; REVERSE_PCT=8; BUBBLE_MIN_SEC=30; BUBBLE_MAX_SEC=60; BUBBLE_HOLD_SEC=5
CHAR_SIZE=28; CHAR_FRAMES=1; RENDER_SCALE=2
BUBBLE_W_PT=180; BUBBLE_FONT_PT=8; DIALOG_MODE=dynamic
[ -f "$PROJ/scripts/params.sh" ] && source "$PROJ/scripts/params.sh"
# 防护：避免空值/0 导致算术异常
[ "$CHAR_FRAMES" -lt 1 ] 2>/dev/null && CHAR_FRAMES=1
[ "$CHAR_SIZE" -lt 1 ] 2>/dev/null && CHAR_SIZE=28
[ "$RENDER_SCALE" -lt 1 ] 2>/dev/null && RENDER_SCALE=2
[ "$BUBBLE_W_PT" -lt 40 ] 2>/dev/null && BUBBLE_W_PT=180
[ "$BUBBLE_FONT_PT" -lt 4 ] 2>/dev/null && BUBBLE_FONT_PT=8
[ -z "$DIALOG_MODE" ] && DIALOG_MODE=dynamic
QUEUE="/tmp/mtmr-dialog-queue.txt"
hold_beats=$((BUBBLE_HOLD_SEC * 4))
min_beats=$((BUBBLE_MIN_SEC * 4))
range_beats=$(( (BUBBLE_MAX_SEC - BUBBLE_MIN_SEC) * 4 ))
[ "$range_beats" -le 0 ] && range_beats=1
reverse_end=$((PAUSE_PCT + REVERSE_PCT))

# state: "pos dir bubble_left next_bubble"
state=$(cat "$STATE" 2>/dev/null || echo "20 right 0 $min_beats")
pos=$(echo "$state" | cut -d' ' -f1)
dir=$(echo "$state" | cut -d' ' -f2)
bubble_left=$(echo "$state" | cut -d' ' -f3)
next_bubble=$(echo "$state" | cut -d' ' -f4)

xs=(20 29 38 47 56 65 74 83 92 101 110 119 128 137 146 155 164 173 182 191 200 209 218 227 236 245 254 263 272 281 290 299 308 317 326 335 344 352 352 352 352)

# 走一步（共用）
step_forward() {
    local rnd=$((RANDOM % 100))
    if [ "$rnd" -lt "$PAUSE_PCT" ]; then
        new_pos=$pos
    elif [ "$rnd" -lt "$reverse_end" ]; then
        if [ "$dir" = "right" ]; then dir="left"; else dir="right"; fi
        [ "$dir" = "right" ] && new_pos=$((pos + 1)) || new_pos=$((pos - 1))
    else
        [ "$dir" = "right" ] && new_pos=$((pos + 1)) || new_pos=$((pos - 1))
    fi
    if [ "$new_pos" -ge 40 ]; then new_pos=40; dir="left"; fi
    if [ "$new_pos" -le 0 ]; then new_pos=0; dir="right"; fi
}

# 合成 bubble_current.png（角色在 char_x + 右侧带文字对话框）。按 RENDER_SCALE ×物理像素出
compose_bubble() {
    local text="$1"
    local S=$RENDER_SCALE
    local char_x_pt=${xs[$new_pos]}                       # 逻辑点位置
    local char_x=$(( char_x_pt * S ))                     # 物理像素位置
    local char_y=$(( ((30 - CHAR_SIZE) / 2) * S ))
    local char_px=$(( CHAR_SIZE * S ))                    # 角色物理像素尺寸
    local phase=$(( new_pos % CHAR_FRAMES ))
    local char_img="$WALK/char_${char_px}x${char_px}-${phase}.png"
    [ -f "$char_img" ] || char_img="$WALK/char_${char_px}x${char_px}.png"   # fallback：拆帧前的单张副本
    local bh=$(( 16 * S ))                # 气泡物理高（受 widget 30 限制）
    local font_pt=$(( BUBBLE_FONT_PT * S ))
    local canvas_w=$(( 400 * S ))
    local canvas_h=$(( 30 * S ))
    local margin=$(( 10 * S ))
    # 气泡宽按文字实际渲染宽度动态（ImageMagick label 测量，准确且不受 MTMR shell locale 影响）
    local text_px
    text_px=$(convert -font "$FONT" -pointsize $font_pt "label:$text" -format "%w" info: 2>/dev/null)
    [ -z "$text_px" ] && text_px=$(( ${#text} * font_pt ))   # 测量失败回退近似
    local padding=$(( 6 * S ))
    local min_bw=$(( 40 * S ))
    local cap1=$(( BUBBLE_W_PT * S ))                  # 用户设的气泡宽上限
    local cap2=$(( canvas_w - char_px - 2 * margin ))  # 画布能容纳的上限
    local cap=$cap1; [ "$cap2" -lt "$cap" ] && cap=$cap2
    local bw=$(( text_px + 2 * padding ))
    [ "$bw" -lt "$min_bw" ] && bw=$min_bw
    [ "$bw" -gt "$cap" ] && bw=$cap
    local bubble_x
    # 智能定位：能放角色右边就放右，否则放左，不出界不压角色
    if [ $((char_x + char_px + bw + margin)) -le $canvas_w ]; then
        bubble_x=$((char_x + char_px + margin))
    else
        bubble_x=$((char_x - bw - margin))
        [ "$bubble_x" -lt 0 ] && bubble_x=0
    fi
    convert -size ${canvas_w}x${canvas_h} xc:none \
        \( "$WALK/background_${canvas_w}x${canvas_h}.png" -repage +0+0 \) \
        \( "$char_img" -repage +${char_x}+${char_y} \) \
        \( -size ${bw}x${bh} xc:white -bordercolor "#333" -border $S \
           -font "$FONT" -pointsize $font_pt -fill black \
           -gravity center -annotate +0+0 "$text" -repage +${bubble_x}+0 \) \
        -flatten "$BUBBLE_IMG" 2>/dev/null
}

# 选一句对话：动态模式消费生成器队列第一行；空则回退 dialogs.txt 随机选；再空则空格（三层兜底，绝不崩）
pick_dialog() {
    local t=""
    if [ "$DIALOG_MODE" != "static" ] && [ -s "$QUEUE" ]; then
        t=$(head -1 "$QUEUE" 2>/dev/null)
        # 消费：写回第一行之后的（容忍并发/失败，失败则删 tmp 不破坏队列）
        if tail -n +2 "$QUEUE" > "$QUEUE.tmp" 2>/dev/null; then
            mv "$QUEUE.tmp" "$QUEUE" 2>/dev/null || rm -f "$QUEUE.tmp"
        else
            rm -f "$QUEUE.tmp"
        fi
    fi
    if [ -z "$t" ] && [ -s "$DIALOGS" ]; then
        local arr=()
        while IFS= read -r l; do [ -n "$l" ] && arr+=("$l"); done < "$DIALOGS"
        local cnt=${#arr[@]}
        [ "$cnt" -gt 0 ] && t="${arr[$((RANDOM % cnt))]}"
    fi
    [ -z "$t" ] && t=" "
    printf '%s' "$t"
}

# ---- 浮现中：走一步 + 用同一句对话重新合成 ----
if [ "$bubble_left" -gt 0 ]; then
    step_forward
    text=$(cat "$BUBBLE_TEXT" 2>/dev/null || echo " ")
    compose_bubble "$text"
    bubble_left=$((bubble_left - 1))
    echo "$new_pos $dir $bubble_left $next_bubble" > "$STATE"
    echo "bubble"
    exit
fi

# ---- 未浮现：走一步 + 浮现倒计时 ----
step_forward

next_bubble=$((next_bubble - 1))
if [ "$next_bubble" -le 0 ]; then
    # 触发浮现：选一句（动态模式消费队列 / 静态读 dialogs.txt，统一走 pick_dialog）
    text=$(pick_dialog)
    echo "$text" > "$BUBBLE_TEXT"
    compose_bubble "$text"
    bubble_left=$hold_beats
    next_bubble=$((min_beats + RANDOM % range_beats))
    echo "$new_pos $dir $bubble_left $next_bubble" > "$STATE"
    echo "bubble"
    exit
fi

# 正常走路
frame=$((new_pos + 1))
padded=$(printf "%02d" "$frame")
echo "$new_pos $dir 0 $next_bubble" > "$STATE"
echo "walk${padded}"
