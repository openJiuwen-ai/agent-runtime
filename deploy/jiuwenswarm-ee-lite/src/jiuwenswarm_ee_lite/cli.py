# coding: utf-8
"""命令行入口：``jiuwenswarm-lite start|stop|status|env``。

用法（在本项目根目录或任意目录执行）::

    jiuwenswarm-lite start              # 全栈启动（identity→manager→runtime→gateway）
    jiuwenswarm-lite start runtime gateway   # 只启动指定服务
    jiuwenswarm-lite stop               # 全栈停止（逆序）
    jiuwenswarm-lite status             # 常驻服务状态表(含前端进程) + agentserver 子进程表
    jiuwenswarm-lite status --watch     # 持续刷新:每秒重新查询并原地重绘,Ctrl+C 退出
    jiuwenswarm-lite env                # 打印各服务 env 注入表（排障用）

所有数据（SQLite/日志/工作区）落 ``./data``（或 ``JIUWENSWARM_EE_LITE_HOME``）。

CLI 输出一律走 ``click.echo``（G.LOG.02：不用 print 做输出）；click 在**调用期**
解析 ``sys.stdout``，``--watch`` 整屏重绘与 ``redirect_stdout`` 捕获语义不变。
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import io
import logging
import os
import sys
import time

import click

from .config import LiteConfig
from . import frontend as fe
from .launcher import LauncherError, start, status, stop
from .pods import PodsScanError, scan_agentserver_processes
from .spec import SERVICE_ORDER, build_specs


def _setup_logging(quiet: bool) -> None:
    # Windows 控制台默认 GBK，中文错误信息乱码——统一 UTF-8 输出。
    for stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(AttributeError):
            stream.reconfigure(encoding="utf-8", errors="replace")
    logging.basicConfig(
        level=logging.WARNING if quiet else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def _cmd_start(args: argparse.Namespace) -> int:
    cfg = LiteConfig.from_env()
    started = asyncio.run(start(cfg, args.services))
    if started:
        click.echo(f"started: {', '.join(started)}")
    else:
        click.echo("all requested services already running")
    return 0


def _cmd_stop(args: argparse.Namespace) -> int:
    cfg = LiteConfig.from_env()
    stopped = asyncio.run(stop(cfg, args.services))
    if stopped:
        click.echo(f"stopped: {', '.join(stopped)}")
    else:
        click.echo("nothing to stop")
    return 0


def _print_agentserver_table(cfg: LiteConfig) -> None:
    """status 第二张表：agentserver 子进程（runtime 按需拉起,瞬态）。

    同 pod 两行是 Windows venv 正常形态:main=真实解释器（干活的就是它）,
    shim=venv 启动器壳（拉起 main 后原地等待,杀树时一起带走）——
    不是两个 agentserver。
    """
    click.echo()
    try:
        pods = scan_agentserver_processes(cfg)
    except PodsScanError as exc:
        click.echo(f"agentserver 进程: 无法感知（{exc}）")
        return
    n_pods = len({r["pod_id"] for r in pods} - {"?"})
    tail = "" if pods else "  ← 空闲期常态;聊天时 runtime 按需拉起,空闲约 60s 自动回收"
    click.echo(f"agentserver 进程: {len(pods)}（{n_pods} pod）{tail}")
    if pods:
        pw = max(len(str(r["pod_id"])) for r in pods) + 2
        click.echo(f"  {'pod_id':<{pw}}{'pid':<8}{'role':<7}{'sse':<7}{'ws':<7}started")
        for r in pods:
            started = (time.strftime("%H:%M:%S", time.localtime(r["created"]))
                       if r["created"] else "-")
            click.echo(f"  {str(r['pod_id']):<{pw}}{r['pid']:<8}"
                       f"{str(r['role']):<7}"
                       f"{str(r['sse_port'] or '-'):<7}{str(r['ws_port'] or '-'):<7}"
                       f"{started}")


def _render_status_block(cfg: LiteConfig) -> tuple[str, bool]:
    """渲染 status 全部输出（服务表〔含前端进程〕 + agentserver 表）。

    复用一次性打印路径（redirect_stdout 捕获；click.echo 调用期解析
    sys.stdout，捕获依旧成立），保证 ``--watch`` 与单次 status 的输出
    完全同构。返回 (文本, 是否有服务未就绪)。
    """
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rows = asyncio.run(status(cfg))
        # 前端进程（manager_web/user_web，frontend.pid.json 独立簿记）并入
        # 同一张表：从未构建过的前端不出行，构建过未拉起的显示 stopped。
        fe_rows = asyncio.run(fe.status(cfg))
        all_rows = [*rows, *fe_rows]
        width = max(len(str(r["service"])) for r in all_rows) + 2
        click.echo(f"{'service':<{width}}{'state':<10}{'pid':<8}port")
        for r in all_rows:
            click.echo(f"{str(r['service']):<{width}}{str(r['state']):<10}"
                       f"{str(r['pid'] or '-'):<8}{r['port']}")
        _print_agentserver_table(cfg)
        # 退出码只看核心五服务：前端是可选进程，未启动属常态，
        # 不应让脚本化 readiness 判定（exit 0）被可选进程拖红。
        any_down = any(r["state"] != "running" for r in rows)
    return buf.getvalue(), any_down


def _enable_ansi() -> bool:
    """终端是否可按 ANSI 光标控制重绘（Windows 控制台需开 VT，其余天然支持）。"""
    if os.name != "nt":
        return True
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)   # STD_OUTPUT_HANDLE
        mode = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return True   # 非 Windows 原生控制台（mintty pty/管道），ANSI 由终端侧解释
        return bool(kernel32.SetConsoleMode(
            handle, mode.value | 0x0004))   # ENABLE_VIRTUAL_TERMINAL_PROCESSING
    except Exception:
        return False


WATCH_INTERVAL_S = 1.0   # --watch 刷新间隔


def _watch_status(cfg: LiteConfig) -> int:
    """持续刷新模式：每秒整屏清空后重绘当次快照（对齐经典 watch 命令）。

    不用"光标上移 N 行"精确回位——输出含中文宽字符长行,在窄窗口折行后
    逻辑行数与屏幕行数不一致会造成每帧漂移。整屏清(ANSI 2J/H 或 cls)
    对折行/窗口宽度免疫,任何环境都严格覆盖。
    """
    ansi = _enable_ansi()
    try:
        while True:
            tick = time.monotonic()
            text, _any_down = _render_status_block(cfg)
            stamp = time.strftime("%Y-%m-%d %H:%M:%S")
            block = f"[watch] 每秒刷新, Ctrl+C 退出  最后刷新 {stamp}\n{text}"
            if not block.endswith("\n"):
                block += "\n"
            if ansi:
                click.echo("\x1b[2J\x1b[H", nl=False)   # 清屏 + 光标回左上角
            else:
                os.system("cls" if os.name == "nt" else "clear")
            click.echo(block, nl=False)   # click.echo 自带 flush
            # 查询耗时从周期里扣除,保证刷新节奏稳定在 1s
            time.sleep(max(0.0, WATCH_INTERVAL_S - (time.monotonic() - tick)))
    except KeyboardInterrupt:
        click.echo("\nwatch 已退出。")
        return 0


def _cmd_status(args: argparse.Namespace) -> int:
    cfg = LiteConfig.from_env()
    if getattr(args, "watch", False):
        return _watch_status(cfg)
    text, any_down = _render_status_block(cfg)
    click.echo(text, nl=False)
    return 1 if any_down else 0


def _cmd_env(args: argparse.Namespace) -> int:
    cfg = LiteConfig.from_env()
    for spec in build_specs(cfg, args.services):
        click.echo(f"[{spec.name}] port={spec.port} ready=GET {spec.ready_path or '(tcp)'}")
        click.echo(f"  cmd: {' '.join(spec.cmd)}")
        for key in sorted(spec.env):
            click.echo(f"  {key}={spec.env[key]}")
        if spec.unset:
            click.echo(f"  (unset) {', '.join(spec.unset)}")
    return 0


def _cmd_frontend(args: argparse.Namespace) -> int:
    cfg = LiteConfig.from_env()
    action = args.action
    names = args.services or None
    if action == "build":
        built = fe.build(cfg, names, force=args.force)
        click.echo(f"built: {', '.join(built) if built else '(nothing)'}")
    elif action == "start":
        # 每次完整更新:在源码目录原地重新构建 → 拉起。
        fe.build(cfg, names, force=True)
        asyncio.run(fe.start(cfg, names))
        rows = asyncio.run(fe.status(cfg))
        click.echo()
        _print_rows(rows)
        for r in rows:
            if r["state"] == "running" and r["service"] == "manager_web":
                click.echo(f"管理面前端: {cfg.manager_frontend_url}"
                           f"  (登录 admin,{cfg.admin_password};反代 /api→manager"
                           f" /idp→identity /ws→gateway)")
            elif r["state"] == "running" and r["service"] == "user_web":
                click.echo(f"用户面前端: {cfg.user_frontend_url}"
                           f"  (用户面聊天入口;反代 /api,/ws→gateway)")
    elif action == "stop":
        stopped = asyncio.run(fe.stop(cfg, names))
        click.echo(f"stopped: {', '.join(stopped) if stopped else '(nothing)'}")
    elif action == "status":
        rows = asyncio.run(fe.status(cfg))
        _print_rows(rows)
        if not rows:
            click.echo("(前端从未构建过,先执行: jiuwenswarm-lite frontend start)")
    return 0


def fe_url(cfg: LiteConfig, name: str) -> str:
    if name == "manager_web":
        return (f"{cfg.manager_frontend_url}  (登录 admin / "
                f"{'$LITE_ADMIN_PASSWORD' if os.getenv('LITE_ADMIN_PASSWORD') else 'admin123'},"
                f" 反代 /api→manager /idp→identity /ws→gateway)")
    if name == "user_web":
        return f"{cfg.user_frontend_url}  (用户面聊天入口,反代 /api,/ws→gateway)"
    return ""


def _print_rows(rows: list[dict[str, object]]) -> None:
    if not rows:
        return
    width = max(len(str(r["service"])) for r in rows) + 2
    click.echo(f"{'service':<{width}}{'state':<10}{'pid':<8}port")
    for r in rows:
        click.echo(f"{str(r['service']):<{width}}{str(r['state']):<10}"
                   f"{str(r['pid'] or '-'):<8}{r['port']}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="jiuwenswarm-lite",
        description="jiuwenswarm 企业版轻量单机部署：进程级全栈编排",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def _add(name: str, help_: str) -> argparse.ArgumentParser:
        p = sub.add_parser(name, help=help_)
        p.add_argument(
            "services", nargs="*", metavar="SERVICE",
            help=f"服务名（可选；缺省全部：{' → '.join(SERVICE_ORDER)}）")
        return p

    _add("start", "按序启动（幂等，已在跑的跳过）").set_defaults(func=_cmd_start)
    _add("stop", "逆序停止（幂等）").set_defaults(func=_cmd_stop)
    p_status = sub.add_parser("status", help="各服务运行状态(--watch 持续刷新)")
    p_status.add_argument(
        "--watch", "-w", action="store_true",
        help="持续刷新:每秒重新查询并原地重绘(清理上次输出),Ctrl+C 退出")
    p_status.set_defaults(func=_cmd_status)
    p_env = sub.add_parser("env", help="打印各服务启动命令与 env 注入表")
    p_env.add_argument("services", nargs="*", metavar="SERVICE")
    p_env.set_defaults(func=_cmd_env)

    p_fe = sub.add_parser(
        "frontend", help="管理面/用户面前端: 自动克隆 jiuwenclaw→build→start"
                         "(:19003/:19012,源码原地构建)")
    fe_sub = p_fe.add_subparsers(dest="action", required=True)
    for act, help_ in (
            ("build", "在源码目录 npm install + npm run build"),
            ("start", "重新构建 → 拉起前端进程(每次完整更新)"),
            ("stop", "停止前端进程"),
            ("status", "前端进程状态表")):
        p = fe_sub.add_parser(act, help=help_)
        p.add_argument("services", nargs="*", metavar="FE",
                       help="可选: manager | user(缺省全部)")
        if act in ("build", "start"):
            p.add_argument("--force", action="store_true",
                           help="build=强制重建(缺省 dist 已存在跳过)")
    p_fe.set_defaults(func=_cmd_frontend)

    args = parser.parse_args(argv)
    _setup_logging(quiet=getattr(args, "command", "") == "status")
    try:
        return args.func(args)
    except (LauncherError, ValueError) as exc:
        # ValueError: LiteConfig env 解析失败（原 SystemExit 语义,G.ERR.11 改抛）
        click.echo(f"ERROR: {exc}", err=True)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
