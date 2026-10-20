#!/usr/bin/env bash
# common.sh —— 日志函数
# error 输出日志后直接退出脚本，调用方无需再处理返回值

[ -n "${_DEPLOY_COMMON_SH:-}" ] && return 0
_DEPLOY_COMMON_SH=1

info() { echo -e "\033[36m=== $@ ===\033[0m"; }
success() { echo -e "\033[32m✅ $@\033[0m"; }
warning() { echo -e "\033[33m⚠️  $@\033[0m"; }
error() { echo -e "\033[31m❌ $@\033[0m"; exit 1; }
