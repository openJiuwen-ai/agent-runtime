#!/usr/bin/env bash
[ -n "${_DEPLOY_ENVFILE_HANDLER_SH:-}" ] && return 0
_DEPLOY_ENVFILE_HANDLER_SH=1

# envfile_handler.sh —— .env.custom 读取

# 从 env 文件逐行读键值对写入目标关联数组（值可带双引号，支持跨行）
read_env_from_file() { # $1=env文件 $2=目标关联数组名
  local env_file="$1"
  local -n target_array="$2"

  [ -f "${env_file}" ] || error "Config file not found: ${env_file}"

  local key="" buffer="" in_quote=0

  while IFS= read -r line || [[ -n "$line" ]]; do
    local trimmed_line="${line#"${line%%[![:space:]]*}"}"
    if [[ $in_quote -eq 0 && ($trimmed_line == \#* || -z "${trimmed_line}") ]]; then
      continue
    fi

    if (( in_quote )); then
      buffer+=$'\n'"$line"
    else
      buffer="$line"
    fi

    if [[ $in_quote -eq 0 && $buffer =~ ^([A-Za-z0-9_]+)=\"(.*) ]]; then
      key="${BASH_REMATCH[1]}"
      if [[ ${BASH_REMATCH[2]} =~ (.*)\"[[:space:]]*$ ]]; then
        target_array["$key"]="${BASH_REMATCH[1]}"
        in_quote=0
      else
        buffer="${BASH_REMATCH[2]}"
        in_quote=1
      fi
      continue
    fi

    if (( in_quote )); then
      if [[ $buffer =~ (.*)\"[[:space:]]*$ ]]; then
        target_array["$key"]="${BASH_REMATCH[1]}"
        in_quote=0
        key=""
        buffer=""
      fi
      continue
    fi

    if [[ $buffer =~ ^([A-Za-z0-9_]+)=(.*) ]]; then
      local k="${BASH_REMATCH[1]}" v="${BASH_REMATCH[2]}"
      v="${v#\"}"
      v="${v%\"}"
      target_array["$k"]="$v"
    fi

    buffer=""
  done < "$env_file"
}
