#!/bin/bash
# 统计单块硬盘上 umi 数据段（episode）的总时长。
#
# 用法: umi-total-duration.sh 挂载点 [--json] [--by-group] [--by-episode] [--list-anomalies]
#
# 唯一必需的输入是硬盘挂载点（例如 /media/agi123/新加卷）。
#
# 输出的时长与 conflux 分组上报中的 total_duration 是同一个量：把硬盘上每个 umi
# 数据段 meta_info.json 里的 duration 按段四舍五入到整秒后求和。本脚本只读取硬盘，
# 不上传、不上报、不修改任何数据。
#
# 遍历深度、排除规则、umi 类型判定和按段取整都与流水线保持一致
# （pkg/scanner/scanner.go 和 pkg/umiprocessor/umiprocessor.go），因此这里的总时长
# 与流水线对同一块硬盘上报的数值相同。凡是流水线会丢弃的数据段，这里同样不计入总数
# —— 但会计数并列出，因为一个看起来"干净"的少算结果，比一个说明了自己漏掉什么的
# 小结果更危险。
#
# 时长展示沿用 pkg/notifier/feishu.go 的 formatHM 格式（72h14m），标签沿用飞书卡片的
# “数据时长”，以便与通知卡片对照。
#
# 退出码: 0 = 硬盘完整读取；1 = 出现读取错误，总时长偏小（否则一块正在损坏的硬盘
# 会被当成空盘）；2 = 用法错误。

set -u
set -o pipefail

usage() {
	cat <<'EOF'
用法: umi-total-duration.sh 挂载点 [--json] [--by-group] [--by-episode] [--list-anomalies]

统计单块硬盘上 umi 数据段的总时长。

位置参数:
  挂载点            硬盘挂载点，例如 /media/agi123/新加卷

可选参数:
  --json            输出机器可读的 JSON
  --by-group        按 (device_id, group_id) 分组细分总时长
  --by-episode      在总时长之后逐个列出每个数据段的时长（可能有数百行），
                    按路径排序；JSON 模式下输出 episodes 数组
  --list-anomalies  列出所有被跳过的数据段（读取错误、meta 损坏、缺少 AID、
                    超出扫描深度）。其中包括无有效时长的数据段（默认不显示）。
  -h, --help        显示本帮助并退出

退出码: 0 = 完整读取，1 = 存在读取错误（总时长偏小），2 = 用法错误
EOF
}

ROOT=""
OPT_JSON=0
OPT_BY_GROUP=0
OPT_BY_EPISODE=0
OPT_LIST=0

while [ $# -gt 0 ]; do
	case "$1" in
	--json) OPT_JSON=1 ;;
	--by-group) OPT_BY_GROUP=1 ;;
	--by-episode) OPT_BY_EPISODE=1 ;;
	--list-anomalies) OPT_LIST=1 ;;
	-h | --help)
		usage
		exit 0
		;;
	--)
		shift
		[ $# -gt 0 ] && ROOT="$1"
		break
		;;
	-*)
		printf 'umi-total-duration.sh: 未知选项: %s\n\n' "$1" >&2
		usage >&2
		exit 2
		;;
	*)
		if [ -n "$ROOT" ]; then
			printf 'umi-total-duration.sh: 多余的参数: %s\n\n' "$1" >&2
			usage >&2
			exit 2
		fi
		ROOT="$1"
		;;
	esac
	shift
done

if [ -z "$ROOT" ]; then
	usage >&2
	exit 2
fi

# 去掉结尾的斜杠，但保留根目录 "/" 本身。
while [ "${#ROOT}" -gt 1 ] && [ "${ROOT%/}" != "$ROOT" ]; do
	ROOT="${ROOT%/}"
done

if [ ! -d "$ROOT" ]; then
	printf 'umi-total-duration.sh: 不是目录: %s\n' "$ROOT" >&2
	exit 2
fi

# scanner.ScanDrive 把每个顶层目录当作数据段本身、或用
# findEpisodes(depth=0, maxDepth=3) 向下遍历的容器。也就是说挂载点下 1 到 4 层的数据段
# 会被采集，更深的对流水线不可见，因此这里同样不计入总时长。多探测一层，纯粹是为了能把
# 越过这条边界的数据段报出来而不是无声丢弃 —— 它们只会被列出，绝不参与求和，所以比这更深
# 的错放目录仍然可能存在且不会被发现。
MAX_EPISODE_DEPTH=4
DEPTH_PROBE=1

# find 报告 meta_info.json 的 %d = 数据段深度 + 1。
FIND_MAXDEPTH=$((MAX_EPISODE_DEPTH + DEPTH_PROBE + 1))

FIND_ERRS="$(mktemp)"
trap 'rm -f "$FIND_ERRS"' EXIT

# 与 config.DefaultExcludePatterns 对应。点号开头的名字由 -name '.*' 分支剪掉，
# 对应 scanner.IsExcludedName 在匹配模式之前先排除它们的行为。
#
# 用 -prune 剪枝（而不是事后过滤）才能让被排除目录的内容完全不进入遍历，
# 与 Go scanner 从不进入这类目录的行为一致。
#
# %d 是 meta_info.json 自身的深度，%h 是数据段目录。无法列出的目录会写到 stderr，
# 被捕获下来，这样一块正在损坏的硬盘会被报错，而不是被读成空盘。
find "$ROOT" -mindepth 1 -maxdepth "$FIND_MAXDEPTH" \
	\( -name '.*' \
	-o -name 'Thumbs.db' \
	-o -name 'System Volume Information' \
	-o -name '$RECYCLE.BIN' \
	-o -name 'RECYCLER' \) -prune \
	-o -type f -name meta_info.json -printf '%d\t%h\n' \
	2>"$FIND_ERRS" |
	LC_ALL=C sort -t"$(printf '\t')" -k2,2 |
	awk -F'\t' \
		-v root="$ROOT" \
		-v max_depth="$MAX_EPISODE_DEPTH" \
		-v want_json="$OPT_JSON" \
		-v want_groups="$OPT_BY_GROUP" \
		-v want_episodes="$OPT_BY_EPISODE" \
		-v want_list="$OPT_LIST" \
		-v find_errs="$FIND_ERRS" \
		'
# ---------------------------------------------------------------- 辅助函数

function jesc(s,   out) {
	out = s
	gsub(/\\/, "\\\\", out)
	gsub(/"/, "\\\"", out)
	gsub(/\n/, "\\n", out)
	gsub(/\r/, "\\r", out)
	gsub(/\t/, "\\t", out)
	return out
}

# 取出 JSON 字符串值: "key": "value"。缺失时返回 ""。
function jstr(content, key,   re, m, v) {
	re = "\"" key "\"[ \t\r\n]*:[ \t\r\n]*\"[^\"]*\""
	if (match(content, re) == 0) return ""
	m = substr(content, RSTART, RLENGTH)
	# 去掉冒号及值的起始引号。
	sub(/^"[^"]*"[ \t\r\n]*:[ \t\r\n]*"/, "", m)
	v = m
	sub(/"$/, "", v)
	return v
}

function trim(s) {
	gsub(/^[ \t\r\n]+|[ \t\r\n]+$/, "", s)
	return s
}

# umiprocessor.DurationSeconds: 小数秒 -> 整秒。
#
# 不可用的输入映射为 0（"未知"）而不是一个错误的数值，因此坏数据会被排除在总和之外、
# 并作为"缺失"报出来，而不是悄悄把总数抬高。dur_reason 说明原因，可用时为 ""。
function duration_seconds(content,   re, m, v) {
	dur_reason = ""
	re = "\"duration\"[ \t\r\n]*:[ \t\r\n]*-?([0-9]+\\.?[0-9]*|\\.[0-9]+)([eE][-+]?[0-9]+)?"
	if (match(content, re) == 0) {
		# 要么字段缺失，要么存在但不是数值（字符串/null/布尔/对象）。
		if (match(content, "\"duration\"[ \t\r\n]*:") > 0)
			dur_reason = "duration 非数值"
		else
			dur_reason = "缺少 duration 字段"
		return 0
	}
	m = substr(content, RSTART, RLENGTH)
	sub(/^"duration"[ \t\r\n]*:[ \t\r\n]*/, "", m)
	v = m + 0
	if (v <= 0) { dur_reason = sprintf("duration <= 0（%s）", m); return 0 }
	# maxEpisodeSeconds: 单个数据段可信长度的上限（约 11.6 天）。
	# 超过它就是坏数据，而不是一个很长的数据段。
	if (v > 1000000) { dur_reason = sprintf("duration 超出合理范围（%s 秒）", m); return 0 }
	return int(v + 0.5)
}

# scanner.normalizeRobotType，仅覆盖本脚本需要区分的类型。
function normalize_robot_type(raw,   lower) {
	lower = trim(raw)
	lower = tolower(lower)
	if (lower == "umi" || lower == "umi_master") return "umi"
	if (lower ~ /^g2/) return "g2"
	if (lower ~ /^gopro/) return "gopro"
	return ""
}

function note(msg) { notes[++n_notes] = msg }

# 与 pkg/notifier/feishu.go 的 formatHM 一致: 72h14m / 72h / 14m。
function fmt_hm(total,   h, m) {
	total = int(total)
	h = int(total / 3600)
	m = int((total % 3600) / 60)
	if (h > 0 && m > 0) return sprintf("%dh%dm", h, m)
	if (h > 0) return sprintf("%dh", h)
	return sprintf("%dm", m)
}

# 单个数据段较短，用 分秒 展示，避免 formatHM 把不足一分钟的段显示成 0m。
function fmt_hm_s(total,   h, m, sec) {
	total = int(total)
	h = int(total / 3600)
	m = int((total % 3600) / 60)
	sec = total % 60
	if (h > 0) return sprintf("%dh%02dm%02ds", h, m, sec)
	return sprintf("%dm%02ds", m, sec)
}

# ---------------------------------------------------------------- 扫描

{
	depth = $1 + 0
	dir = $2
	# 数据段目录比它的 meta_info.json 高一层。
	ep_depth = depth - 1

	# Go scanner 一旦判定某目录是数据段就停止向下遍历，因此嵌套在数据段内部的数据段
	# 永远不会被访问。输入按路径排序，所以任何祖先数据段都已经处理过了。
	if (n_eps > 0) {
		for (i = n_eps; i >= 1; i--) {
			if (index(dir, ep_paths[i] "/") == 1) { nested++; next }
		}
	}
	ep_paths[++n_eps] = dir

	if (ep_depth > max_depth) {
		too_deep[++n_too_deep] = dir
		next
	}

	metafile = dir "/meta_info.json"
	content = ""
	rc = (getline line < metafile)
	if (rc < 0) {
		# EIO 之类: 硬盘没能把文件交出来。这与内容无法解析要分开处理，因为两者含义相反
		# —— JSON 坏了是可读硬盘上的数据问题，而读取错误意味着这块硬盘上的一切统计都偏小。
		close(metafile)
		unreadable_path[++n_unreadable] = dir
		next
	}
	while (rc > 0) {
		content = content line "\n"
		rc = (getline line < metafile)
	}
	close(metafile)
	if (rc < 0) {
		unreadable_path[++n_unreadable] = dir
		next
	}

	# 是不是一个 JSON 对象。曾经出现过 meta_info.json 是二进制垃圾的数据段，
	# 流水线把这类计为损坏而不是直接失败。
	probe = trim(content)
	if (probe !~ /^\{/ || probe !~ /\}$/) {
		malformed_path[++n_malformed] = dir
		malformed_why[n_malformed] = (probe ~ /^\[/) ? "不是 JSON 对象" : "JSON 无法解析"
		next
	}

	robot_type = normalize_robot_type(jstr(content, "robot_type"))
	if (robot_type != "umi") {
		label = robot_type
		if (label == "") {
			raw = jstr(content, "robot_type")
			label = (raw == "") ? "未知（缺失）" : ("未知（" raw "）")
		}
		other_types[label]++
		next
	}

	seconds = duration_seconds(content)
	reason = dur_reason

	# scanner.appendEpisodeIfUploadable 会丢弃没有 AID 的数据段，它的时长永远不会
	# 进入 conflux 上报，因此这里也不计入总时长。
	aid = trim(jstr(content, "AID"))
	if (aid == "") {
		no_aid_path[++n_no_aid] = dir
		no_aid_secs[n_no_aid] = seconds
		no_aid_total += seconds
		next
	}

	device_id = trim(jstr(content, "device_sn"))
	if (device_id == "") device_id = aid
	group_id = trim(jstr(content, "group_id"))

	# 无有效时长的数据段（<= 0、非数值、缺失、超范围）不参与展示，也不计入数据段数和分组，
	# 只记录下来用于注意事项里的计数。
	if (reason != "") {
		missing_path[++n_missing] = dir
		missing_why[n_missing] = reason
		next
	}

	total += seconds
	n_umi++
	umi_path[n_umi] = dir
	umi_secs[n_umi] = seconds
	umi_dev[n_umi] = device_id
	umi_gid[n_umi] = group_id

	key = device_id SUBSEP group_id
	if (!(key in group_seen)) {
		group_seen[key] = 1
		group_dev[++n_groups] = device_id
		group_gid[n_groups] = group_id
	}
	group_secs[key] += seconds
	group_eps[key]++
}

# ---------------------------------------------------------------- 输出

END {
	# 硬盘拒绝列出的目录。与读不出的数据段分开计数: 两者都意味着遍历不完整。
	while ((getline errline < find_errs) > 0) {
		if (errline ~ /^[ \t]*$/) continue
		unreadable_dir[++n_unreadable_dirs] = errline
	}
	close(find_errs)

	unreliable = (n_unreadable > 0 || n_unreadable_dirs > 0)
	hm = fmt_hm(total)

	if (want_json) {
		# JSON 的键保持英文，便于被其他程序消费。
		printf "{\n"
		printf "  \"mount_point\": \"%s\",\n", jesc(root)
		printf "  \"total_duration_seconds\": %.0f,\n", total
		printf "  \"total_duration_hm\": \"%s\",\n", hm
		printf "  \"umi_episodes\": %d,\n", n_umi
		printf "  \"groups\": %d,\n", n_groups
		printf "  \"episodes_missing_duration\": %d,\n", n_missing
		printf "  \"episodes_malformed_meta\": %d,\n", n_malformed
		printf "  \"episodes_unreadable\": %d,\n", n_unreadable
		printf "  \"unreadable_directories\": %d,\n", n_unreadable_dirs
		printf "  \"episodes_missing_aid\": %d,\n", n_no_aid
		printf "  \"episodes_below_scan_depth\": %d,\n", n_too_deep
		printf "  \"non_umi_episodes\": {"
		sep = ""
		for (t in other_types) {
			printf "%s\n    \"%s\": %d", sep, jesc(t), other_types[t]
			sep = ","
		}
		printf "%s},\n", (sep == "" ? "" : "\n  ")

		if (want_groups) {
			printf "  \"by_group\": ["
			sep = ""
			for (i = 1; i <= n_groups; i++) {
				key = group_dev[i] SUBSEP group_gid[i]
				printf "%s\n    {\"device_id\": \"%s\", \"group_id\": \"%s\", \"episodes\": %d, \"seconds\": %.0f, \"hm\": \"%s\"}",
					sep, jesc(group_dev[i]), jesc(group_gid[i]), group_eps[key], group_secs[key], fmt_hm(group_secs[key])
				sep = ","
			}
			printf "%s],\n", (sep == "" ? "" : "\n  ")
		}

		if (want_episodes) {
			printf "  \"episodes\": ["
			sep = ""
			for (i = 1; i <= n_umi; i++) {
				printf "%s\n    {\"path\": \"%s\", \"device_id\": \"%s\", \"group_id\": \"%s\", \"seconds\": %.0f, \"hm\": \"%s\"}",
					sep, jesc(umi_path[i]), jesc(umi_dev[i]), jesc(umi_gid[i]), umi_secs[i], fmt_hm(umi_secs[i])
				sep = ","
			}
			printf "%s],\n", (sep == "" ? "" : "\n  ")
		}

		if (want_list) {
			printf "  \"missing_duration_paths\": ["
			sep = ""
			for (i = 1; i <= n_missing; i++) {
				printf "%s\n    {\"path\": \"%s\", \"reason\": \"%s\"}", sep, jesc(missing_path[i]), jesc(missing_why[i])
				sep = ","
			}
			printf "%s],\n", (sep == "" ? "" : "\n  ")

			printf "  \"malformed_meta_paths\": ["
			sep = ""
			for (i = 1; i <= n_malformed; i++) {
				printf "%s\n    {\"path\": \"%s\", \"reason\": \"%s\"}", sep, jesc(malformed_path[i]), jesc(malformed_why[i])
				sep = ","
			}
			printf "%s],\n", (sep == "" ? "" : "\n  ")

			printf "  \"unreadable_paths\": ["
			sep = ""
			for (i = 1; i <= n_unreadable; i++) {
				printf "%s\n    \"%s\"", sep, jesc(unreadable_path[i])
				sep = ","
			}
			printf "%s],\n", (sep == "" ? "" : "\n  ")

			printf "  \"unreadable_directory_errors\": ["
			sep = ""
			for (i = 1; i <= n_unreadable_dirs; i++) {
				printf "%s\n    \"%s\"", sep, jesc(unreadable_dir[i])
				sep = ","
			}
			printf "%s],\n", (sep == "" ? "" : "\n  ")

			printf "  \"missing_aid_paths\": ["
			sep = ""
			for (i = 1; i <= n_no_aid; i++) {
				printf "%s\n    \"%s\"", sep, jesc(no_aid_path[i])
				sep = ","
			}
			printf "%s],\n", (sep == "" ? "" : "\n  ")

			printf "  \"below_scan_depth_paths\": ["
			sep = ""
			for (i = 1; i <= n_too_deep; i++) {
				printf "%s\n    \"%s\"", sep, jesc(too_deep[i])
				sep = ","
			}
			printf "%s],\n", (sep == "" ? "" : "\n  ")
		}

		printf "  \"total_is_reliable\": %s\n", (unreliable ? "false" : "true")
		printf "}\n"
		exit (unreliable ? 1 : 0)
	}

	# 中文标签宽度不一，不做列对齐，避免 CJK 字符按字节计算导致错位。
	printf "硬盘：%s\n", root
	printf "数据时长：%s（%.0f 秒）%s\n", hm, total,
		(unreliable ? "  【数据不完整 - 存在读取错误】" : "")
	printf "umi 数据段：%d 个，共 %d 个分组\n", n_umi, n_groups

	if (want_groups && n_groups > 0) {
		printf "\n按 (device_id, group_id) 细分：\n"
		for (i = 1; i <= n_groups; i++) {
			key = group_dev[i] SUBSEP group_gid[i]
			printf "  %-24s %-38s %4d 段  %9.0f 秒  %s\n",
				group_dev[i], (group_gid[i] == "" ? "（无）" : group_gid[i]),
				group_eps[key], group_secs[key], fmt_hm(group_secs[key]) | "LC_ALL=C sort"
		}
		close("LC_ALL=C sort")
	}

	if (want_episodes && n_umi > 0) {
		printf "\n逐个数据段时长（%d 个，按路径排序）：\n", n_umi
		for (i = 1; i <= n_umi; i++)
			printf "  %9.0f 秒  %9s  %s\n", umi_secs[i], fmt_hm_s(umi_secs[i]), umi_path[i]
	}

	# 任何可能让总时长偏小的情况，都明确说出来。
	if (unreliable) {
		# 放在最前面: 读取错误让总时长失去意义，而不只是打个折扣。
		parts = ""
		if (n_unreadable > 0) parts = sprintf("%d 个数据段", n_unreadable)
		if (n_unreadable_dirs > 0) {
			if (parts != "") parts = parts "、"
			parts = parts sprintf("%d 个目录列表", n_unreadable_dirs)
		}
		note(sprintf("读取错误：%s读不出来。硬盘没有交出全部数据，上面的总时长偏小且偏小幅度未知。在采信这个数字之前，请先检查硬盘链路（journalctl -k | grep -i '\''I/O error'\''）。", parts))
	}
	if (n_missing > 0)
		note(sprintf("%d 个 umi 数据段没有有效时长，已从列表和统计中排除（--list-anomalies 查看明细）", n_missing))
	if (n_malformed > 0)
		note(sprintf("%d 个数据段的 meta_info.json 无法解析（类型未知，未计入）", n_malformed))
	if (n_no_aid > 0)
		note(sprintf("%d 个 umi 数据段缺少 AID，流水线不会上传它们（已排除 %.0f 秒）", n_no_aid, no_aid_total))
	if (n_too_deep > 0)
		note(sprintf("%d 个数据段的层级比流水线扫描范围更深，未计入", n_too_deep))
	if (length(other_types) > 0) {
		parts = ""
		for (t in other_types) {
			if (parts != "") parts = parts "、"
			parts = parts sprintf("%s=%d", t, other_types[t])
		}
		note("存在非 umi 数据段，未计入：" parts)
	}

	if (n_notes > 0) {
		printf "\n注意事项：\n"
		for (i = 1; i <= n_notes; i++) printf "  - %s\n", notes[i]
	}

	if (want_list) {
		if (n_missing > 0) {
			printf "\n无有效时长的数据段（%d 个）：\n", n_missing
			for (i = 1; i <= n_missing; i++) printf "  %s  [%s]\n", missing_path[i], missing_why[i]
		}
		if (n_unreadable > 0) {
			printf "\n读取错误（数据段）：\n"
			for (i = 1; i <= n_unreadable; i++) printf "  %s\n", unreadable_path[i]
		}
		if (n_unreadable_dirs > 0) {
			printf "\n读取错误（目录）：\n"
			for (i = 1; i <= n_unreadable_dirs; i++) printf "  %s\n", unreadable_dir[i]
		}
		if (n_malformed > 0) {
			printf "\nmeta_info.json 无法解析：\n"
			for (i = 1; i <= n_malformed; i++) printf "  %s  [%s]\n", malformed_path[i], malformed_why[i]
		}
		if (n_no_aid > 0) {
			printf "\n缺少 AID：\n"
			for (i = 1; i <= n_no_aid; i++) printf "  %s  [%.0f 秒]\n", no_aid_path[i], no_aid_secs[i]
		}
		if (n_too_deep > 0) {
			printf "\n超出扫描深度：\n"
			for (i = 1; i <= n_too_deep; i++) printf "  %s\n", too_deep[i]
		}
	}

	if (n_notes == 0 && n_umi == 0)
		printf "\n在 %s 下没有找到 umi 数据段。\n", root

	exit (unreliable ? 1 : 0)
}
'
