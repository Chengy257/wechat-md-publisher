#!/usr/bin/env bash
# publish.sh — WeChat draft one-click publishing wrapper
#
# Usage:
#   ./publish.sh <markdown_file> [options]
#
# Examples:
#   ./publish.sh article.md
#   ./publish.sh article.md --cover photo.png
#   ./publish.sh article.md --cover photo.png --title "自定义标题"
#   ./publish.sh article.md --gen-cover
#   ./publish.sh article.md --dry-run

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# ── Colors ──
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
NC='\033[0m'

info()  { echo -e "${GREEN}[INFO]${NC} $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*"; }
step()  { echo -e "${CYAN}[STEP]${NC} $*"; }

usage() {
    cat <<EOF
Usage: $0 <markdown_file> [options]

One-click WeChat draft publishing.

Options:
  --cover <path>     Use this image as cover (resized if needed)
  --title <text>     Override article title
  --author <text>    Override article author
  --digest <text>    Override article digest
  --theme <name>     Built-in theme (default/elegant/lapis/simple/tech)
  --style <path>     Custom CSS theme file (overrides --theme)
  --ai-summary       Generate digest via AI when not specified
  --ai-cover         Generate cover image via AI when no cover is available
  --mermaid          Render mermaid diagrams to PNG images
  --dry-run          Preview only, don't push draft
  -h, --help         Show this help

Examples:
  $0 article.md
  $0 article.md --cover photo.png
  $0 article.md --cover photo.png --title "My Title" --theme lapis
  $0 article.md --ai-summary --ai-cover
EOF
}

# ── Parse args ──
MD_FILE=""
COVER_FILE=""
CLI_TITLE=""
CLI_AUTHOR=""
CLI_DIGEST=""
CLI_THEME=""
CLI_STYLE=""
AI_SUMMARY=false
AI_COVER=false
MERMAID=false
DRY_RUN=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --cover)
            [[ $# -lt 2 ]] && { error "--cover requires a path"; exit 1; }
            COVER_FILE="$2"; shift 2 ;;
        --title)
            [[ $# -lt 2 ]] && { error "--title requires a value"; exit 1; }
            CLI_TITLE="$2"; shift 2 ;;
        --author)
            [[ $# -lt 2 ]] && { error "--author requires a value"; exit 1; }
            CLI_AUTHOR="$2"; shift 2 ;;
        --digest)
            [[ $# -lt 2 ]] && { error "--digest requires a value"; exit 1; }
            CLI_DIGEST="$2"; shift 2 ;;
        --theme)
            [[ $# -lt 2 ]] && { error "--theme requires a name"; exit 1; }
            CLI_THEME="$2"; shift 2 ;;
        --style)
            [[ $# -lt 2 ]] && { error "--style requires a path"; exit 1; }
            CLI_STYLE="$2"; shift 2 ;;
        --ai-summary)  AI_SUMMARY=true; shift ;;
        --ai-cover)    AI_COVER=true; shift ;;
        --mermaid)     MERMAID=true; shift ;;
        --dry-run)     DRY_RUN=true; shift ;;
        -h|--help)     usage; exit 0 ;;
        -*)
            error "Unknown option: $1"
            usage; exit 1 ;;
        *)
            if [[ -n "$MD_FILE" ]]; then
                error "Multiple markdown files specified. Only one allowed."
                exit 1
            fi
            MD_FILE="$1"; shift ;;
    esac
done

if [[ -z "$MD_FILE" ]]; then
    error "No markdown file specified."
    usage; exit 1
fi

MD_FILE="$(realpath "$MD_FILE" 2>/dev/null || echo "$MD_FILE")"
if [[ ! -f "$MD_FILE" ]]; then
    error "File not found: $MD_FILE"
    exit 1
fi

MD_BASENAME="$(basename "$MD_FILE" .md)"
MD_DIR="$(dirname "$MD_FILE")"

# ── Step 1: Auto-insert front matter if missing ──
step "1/3  Checking front matter..."

has_front_matter=false
if head -1 "$MD_FILE" | grep -q '^---$'; then
    line_count=$(grep -c '^---$' "$MD_FILE" || true)
    if [[ "$line_count" -ge 2 ]]; then
        has_front_matter=true
    fi
fi

if [[ "$has_front_matter" == "false" ]]; then
    info "No front matter detected. Auto-generating..."

    # Try to extract first heading as title
    heading_title=$(grep -m1 '^##\? ' "$MD_FILE" | sed 's/^#* *//' || true)
    if [[ -n "$heading_title" ]]; then
        derived_title="$heading_title"
    else
        derived_title=$(echo "$MD_BASENAME" | sed 's/[-_]/ /g' | sed 's/\b\(.\)/\u\1/g')
    fi

    file_date=$(stat -c %Y "$MD_FILE" 2>/dev/null || date +%s)
    derived_date=$(date -d "@$file_date" +%Y-%m-%d 2>/dev/null || date +%Y-%m-%d)
    default_author="Cy257"

    fm_block="---
title: \"${derived_title}\"
date: ${derived_date}
author: \"${default_author}\"
---"

    tmp_file="$(mktemp)"
    { echo "$fm_block"; echo ""; cat "$MD_FILE"; } > "$tmp_file"
    mv "$tmp_file" "$MD_FILE"

    info "Front matter inserted:"
    echo "  title:  ${derived_title}"
    echo "  date:   ${derived_date}"
    echo "  author: ${default_author}"
else
    info "Front matter already present. Skipping."
fi

# ── Step 2: Cover image ──
step "2/3  Cover image..."

COVER_PATH=""

# Priority: --cover flag > cover next to md > input/cover.png
if [[ -n "$COVER_FILE" ]]; then
    COVER_FILE="$(realpath "$COVER_FILE" 2>/dev/null || echo "$COVER_FILE")"
    if [[ ! -f "$COVER_FILE" ]]; then
        error "Cover file not found: $COVER_FILE"
        exit 1
    fi
    COVER_PATH="$COVER_FILE"
elif [[ -f "${MD_DIR}/cover.png" ]]; then
    COVER_PATH="${MD_DIR}/cover.png"
elif [[ -f "${SCRIPT_DIR}/input/cover.png" ]]; then
    COVER_PATH="${SCRIPT_DIR}/input/cover.png"
fi

# Compress cover if > 1024KB (WeChat limit)
compress_cover() {
    local src="$1"
    local size_kb
    size_kb=$(($(stat -c %s "$src" 2>/dev/null || echo 1048576) / 1024))
    if [[ $size_kb -gt 1024 ]]; then
        info "Cover too large (${size_kb} KB), compressing..."
        local compressed="${SCRIPT_DIR}/input/cover_compressed.png"
        python3 -c "
from PIL import Image
img = Image.open('$src').convert('RGB')
img = img.resize((900, 383), Image.LANCZOS)
img.save('$compressed', 'JPEG', quality=85, optimize=True)
print(f'Compressed: {__import__(\"os\").path.getsize(\"$compressed\")//1024} KB')
" 2>/dev/null && COVER_PATH="$compressed"
    fi
}

if [[ -n "$COVER_PATH" ]]; then
    compress_cover "$COVER_PATH"
    info "Using cover: ${COVER_PATH}"
fi

# Generate cover via AI if requested and no cover found
if [[ -z "$COVER_PATH" && "$AI_COVER" == "true" ]]; then
    info "No cover found. AI cover generation will be handled by the draft command (--ai-cover)."
fi

# ── Step 3: Push draft ──
step "3/3  Pushing draft..."

DRAFT_ARGS=(--md "$MD_FILE")
[[ -n "$COVER_PATH" ]] && DRAFT_ARGS+=(--cover "$COVER_PATH")
[[ -n "$CLI_TITLE" ]] && DRAFT_ARGS+=(--title "$CLI_TITLE")
[[ -n "$CLI_AUTHOR" ]] && DRAFT_ARGS+=(--author "$CLI_AUTHOR")
[[ -n "$CLI_DIGEST" ]] && DRAFT_ARGS+=(--digest "$CLI_DIGEST")
[[ -n "$CLI_THEME" ]] && DRAFT_ARGS+=(--theme "$CLI_THEME")
[[ -n "$CLI_STYLE" ]] && DRAFT_ARGS+=(--style "$CLI_STYLE")
[[ "$AI_SUMMARY" == "true" ]] && DRAFT_ARGS+=(--ai-summary)
[[ "$AI_COVER" == "true" ]] && DRAFT_ARGS+=(--ai-cover)
[[ "$MERMAID" == "true" ]] && DRAFT_ARGS+=(--mermaid)
[[ "$DRY_RUN" == "true" ]] && DRAFT_ARGS+=(--dry-run)

info "Running: python3 -m wechat_publish.cli draft ${DRAFT_ARGS[*]}"
python3 -m wechat_publish.cli draft "${DRAFT_ARGS[@]}"

echo ""
info "Done! Check your drafts at https://mp.weixin.qq.com → 草稿箱"
