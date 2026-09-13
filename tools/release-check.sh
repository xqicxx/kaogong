#!/bin/bash
# 上线门禁：一条命令跑完上线前该查的全部东西。
#
#   tools/release-check.sh
#
# 退出码非零 = 不能上线。任何一项失败都会说清楚是哪一项。
set -uo pipefail
# cd 失败必须当场退出：这个脚本用的是 set -uo pipefail（故意不加 -e，因为要跑完所有检查
# 再汇总），所以 cd 失败不会中止，脚本会继续在**别的目录**里做检查 ——
# 那可能对着空气报「可以上线」。门禁自己不能有这种洞。
cd "$(cd "$(dirname "$0")/.." && pwd -P)" || {
  echo "  ✗ 进不到仓库根，门禁没法跑" >&2
  exit 1
}
VAULT="$HOME/Documents/Obsidian Vault/考公"
BIN="$HOME/.pi/bin"
PLIST="$HOME/Library/LaunchAgents/com.kaogong.review.plist"

failed=0
pass() { echo "  ✓ $1"; }
fail() {
  echo "  ✗ $1" >&2
  failed=$((failed + 1))
}
section() {
  echo
  echo "── $1 ──"
}

section "1. 自检套件"
if python3 pipeline/selftest.py >/tmp/release-selftest.log 2>&1; then
  pass "$(tail -1 /tmp/release-selftest.log | sed 's/^ *//')"
else
  fail "自检未通过，看 /tmp/release-selftest.log"
  grep "✗" /tmp/release-selftest.log | head -5 | sed 's/^/      /' >&2
fi

section "2. 工具链"
for tool in rectify deink vision-ocr crop vision2md.py kaogong-ocr.sh; do
  if [ -e "$BIN/$tool" ]; then
    pass "$tool 已安装"
  else
    fail "$tool 没装（跑 tools/install.sh）"
  fi
done
for script in vision2md.py kaogong-ocr.sh; do
  if [ -L "$BIN/$script" ] && [ "$(readlink "$BIN/$script")" = "$(pwd)/tools/$script" ]; then
    pass "$script 是指向仓库的软链（改了立即生效）"
  else
    fail "$script 不是软链 —— 改了仓库里的版本不会生效"
  fi
done

section "3. Python 依赖与语法"
if python3 -c "import ast,sys; [ast.parse(open('pipeline/'+f).read()) for f in ('paths.py','errors.py','fsrs.py','cards.py','notify.py','mastery.py','dedup.py','classify.py','answers.py','vault.py','review.py','mistake.py','split.py','selftest.py')]" 2>/dev/null; then
  pass "pipeline/*.py 语法通过"
else
  fail "pipeline 里有语法错误"
fi
for m in paths errors fsrs cards notify mastery dedup classify answers vault review mistake split; do
  if python3 -c "import sys; sys.path.insert(0,'.'); from pipeline import $m" 2>/dev/null; then
    pass "pipeline.$m 可导入"
  else
    fail "pipeline.$m 导不进来"
  fi
done

section "4. Obsidian 目录结构"
for d in 行测/常识判断 行测/言语理解与表达 行测/数量关系 行测/判断推理 行测/资料分析 \
  申论/归纳概括 申论/综合分析 申论/提出对策 申论/贯彻执行 申论/申发论述 错题 _raw; do
  if [ -d "$VAULT/$d" ]; then
    pass "考公/$d"
  else
    fail "缺目录 考公/$d"
  fi
done

section "5. 定时推送"
if [ -f "$PLIST" ] && plutil -lint "$PLIST" >/dev/null 2>&1; then
  pass "launchd 配置有效"
  if launchctl list 2>/dev/null | grep -q com.kaogong.review; then
    pass "launchd 已加载（每天 08:00）"
  else
    fail "launchd 没加载：launchctl load $PLIST"
  fi
  if grep -q "/tmp/" "$PLIST"; then
    fail "日志还写在 /tmp（重启会清）"
  else
    pass "日志不在 /tmp"
  fi
else
  fail "launchd 配置缺失或无效"
fi

section "6. 数据安全"
if [ -d "$HOME/.pi/kaogong" ]; then
  pass "状态目录存在（~/.pi/kaogong）"
else
  fail "缺状态目录"
fi
if python3 -c "import sys; sys.path.insert(0,'.'); from pipeline import vault; print(vault.BACKUP_DIR)" >/dev/null 2>&1; then
  pass "写前快照机制在位"
else
  fail "快照机制取不到"
fi
if git -C "$VAULT" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  pass "vault 在版本控制里"
else
  echo "  ! vault 不在 git 里（不影响运行，但笔记改动只能靠快照回滚）"
fi

section "7. 仓库卫生"
# 直接判命令成败，不要用 $? —— 它指的是「上一条命令」，中间插一行就会变味
if ! git_status=$(git status --porcelain 2>&1); then
  fail "git status 跑不了：$git_status"
elif [ -n "$git_status" ]; then
  fail "有未提交的改动"
else
  pass "工作区干净"
fi
if git ls-files | grep -qE "(__pycache__|\.pyc$)"; then
  fail "仓库里混进了编译缓存"
else
  pass "没有编译缓存"
fi
tracked=$(git ls-files)
if [ -z "$tracked" ]; then
  fail "git ls-files 没返回文件（仓库出问题了？）"
else
  # 先看文件名，再看内容 —— 只查文件名会漏掉写在脚本里的 key
  name_hit=$(printf '%s\n' "$tracked" | grep -iE "(secret|credential|\.env$|id_rsa)" || true)
  # 模式用单引号包，避免里面的引号把整个脚本的字符串搞断
  content_hit=$(printf '%s\n' "$tracked" | xargs grep -lE 'sk-[A-Za-z0-9]{16,}|ghp_[A-Za-z0-9]{20,}|api[_-]?key[[:space:]]*=[[:space:]]*' 2>/dev/null || true)
  if [ -n "$name_hit" ] || [ -n "$content_hit" ]; then
    fail "仓库里疑似有密钥：${name_hit}${content_hit}"
  else
    pass "没有密钥文件，也没扫到硬编码 key"
  fi
fi

section "8. shell 静态检查"
if command -v shellcheck >/dev/null 2>&1; then
  # 只卡 error/warning：style 级别的偏好不该拦上线
  if shellcheck -S warning tools/*.sh; then
    pass "shellcheck 无 error / warning"
  else
    fail "shellcheck 有发现（见上面输出）"
  fi
else
  # 没装就明说跳过了，不要假装通过
  echo "  - 没装 shellcheck，跳过（brew install shellcheck）"
fi

section "结果"
if [ "$failed" -gt 0 ]; then
  echo "  $failed 项未通过 —— 不能上线" >&2
  exit 1
fi
echo "  全部通过，可以上线"
