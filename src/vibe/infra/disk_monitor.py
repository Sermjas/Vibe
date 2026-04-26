from __future__ import annotations

import argparse
import json
import asyncio
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from loguru import logger

from vibe.config import get_config
from vibe.infra.telegram_notify import send_admin_message


@dataclass(frozen=True)
class DiskStatus:
    path: str
    total_bytes: int
    free_bytes: int

    @property
    def free_percent(self) -> float:
        if self.total_bytes <= 0:
            return 0.0
        return (self.free_bytes / self.total_bytes) * 100.0


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_state(path: str) -> dict[str, Any]:
    p = Path(path)
    try:
        if not p.exists():
            return {}
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning(f"Не удалось прочитать state файл {path}: {e}")
        return {}


def _write_state(path: str, state: dict[str, Any]) -> None:
    p = Path(path)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        logger.warning(f"Не удалось записать state файл {path}: {e}")


def check_disk(path: str) -> DiskStatus:
    usage = shutil.disk_usage(path)
    return DiskStatus(path=path, total_bytes=int(usage.total), free_bytes=int(usage.free))


def _run_cmd(cmd: list[str]) -> tuple[int, str]:
    logger.info(f"Выполняю команду: {' '.join(cmd)}")
    p = subprocess.run(cmd, capture_output=True, text=True)
    out = (p.stdout or "") + (("\n" + p.stderr) if p.stderr else "")
    out = out.strip()
    if out:
        logger.info(f"Вывод команды:\n{out}")
    logger.info(f"Код возврата: {p.returncode}")
    return p.returncode, out


def docker_cleanup(*, include_volumes: bool) -> None:
    # Важно: это maintenance задача, не часть бизнес-логики бота.
    base = ["docker", "system", "prune", "-af"]
    if include_volumes:
        base.append("--volumes")
    rc, _ = _run_cmd(base)
    if rc != 0:
        raise RuntimeError("docker system prune завершился с ошибкой")


def _format_bytes(n: int) -> str:
    # Человеко-читаемо для уведомлений.
    units = ["B", "KB", "MB", "GB", "TB", "PB"]
    v = float(max(n, 0))
    for u in units:
        if v < 1024.0 or u == units[-1]:
            return f"{v:.1f} {u}"
        v /= 1024.0
    return f"{v:.1f} PB"


def _level_for_free_percent(free_percent: float, warn: float, critical: float) -> str:
    if free_percent < critical:
        return "critical"
    if free_percent < warn:
        return "warning"
    return "ok"


async def run_once(*, dry_run: bool = False) -> int:
    cfg = get_config()

    monitor_path = getattr(cfg, "disk_monitor_path", "/")
    warn = float(getattr(cfg, "disk_warn_percent", 20.0))
    critical = float(getattr(cfg, "disk_critical_percent", 10.0))
    state_file = getattr(cfg, "disk_monitor_state_file", "/app/data/disk_monitor_state.json")
    enable_prune = bool(getattr(cfg, "disk_monitor_enable_prune", True))
    include_volumes = bool(getattr(cfg, "disk_monitor_prune_volumes", True))

    status = check_disk(monitor_path)
    free_percent = status.free_percent
    level = _level_for_free_percent(free_percent, warn=warn, critical=critical)

    logger.info(
        f"Диск {status.path}: свободно {_format_bytes(status.free_bytes)} из {_format_bytes(status.total_bytes)} "
        f"({free_percent:.2f}%)"
    )

    state = _read_state(state_file)
    last_level = state.get("last_level")
    state["last_check_at"] = _now_iso()
    state["last_free_percent"] = free_percent
    state["last_free_bytes"] = status.free_bytes
    state["last_total_bytes"] = status.total_bytes

    # Уведомляем только при смене уровня, чтобы cron не спамил.
    should_notify = level in {"warning", "critical"} and level != last_level

    if should_notify:
        header = "⚠️ Предупреждение: заканчивается место на диске" if level == "warning" else "🚨 Критично: почти нет места на диске"
        text = (
            f"{header}\n"
            f"Путь: {status.path}\n"
            f"Свободно: {_format_bytes(status.free_bytes)} ({free_percent:.2f}%)\n"
            f"Порог warning: {warn:.0f}%, critical: {critical:.0f}%\n"
            f"Время (UTC): {_now_iso()}"
        )
        try:
            await send_admin_message(
                bot_token=cfg.telegram_bot_token,
                admin_id=cfg.admin_id,
                text=text,
            )
            logger.info("Уведомление администратору отправлено.")
        except Exception as e:
            logger.error(f"Не удалось отправить уведомление администратору: {e}")

    # Очистка Docker при достижении warning/critical
    if level in {"warning", "critical"}:
        if not enable_prune:
            logger.warning("Очистка Docker отключена настройкой disk_monitor_enable_prune.")
        elif dry_run:
            logger.warning("DRY RUN: очистка Docker пропущена.")
        else:
            logger.warning("Свободного места < порога. Запускаю очистку Docker.")
            try:
                docker_cleanup(include_volumes=include_volumes)
                state["last_cleanup_at"] = _now_iso()
                state["last_cleanup_ok"] = True
                logger.info("Очистка Docker завершена успешно.")
            except Exception as e:
                state["last_cleanup_at"] = _now_iso()
                state["last_cleanup_ok"] = False
                state["last_cleanup_error"] = str(e)
                logger.error(f"Очистка Docker завершилась с ошибкой: {e}")

    state["last_level"] = level
    _write_state(state_file, state)
    return 0


def _configure_logging(log_path: str | None) -> None:
    logger.remove()
    logger.add(sys.stdout, level="INFO", enqueue=True, backtrace=False, diagnose=False)
    if log_path:
        Path(log_path).parent.mkdir(parents=True, exist_ok=True)
        logger.add(
            log_path,
            level="INFO",
            rotation="5 MB",
            retention=5,
            enqueue=True,
            backtrace=False,
            diagnose=False,
        )


def run() -> None:
    parser = argparse.ArgumentParser(description="Мониторинг диска и очистка Docker при низком free space.")
    parser.add_argument("--dry-run", action="store_true", help="Не выполнять docker prune, только логировать.")
    args = parser.parse_args()

    cfg = get_config()
    log_path = getattr(cfg, "disk_monitor_log_path", "/app/data/disk_monitor.log")
    _configure_logging(log_path)

    try:
        raise SystemExit(asyncio.run(run_once(dry_run=args.dry_run)))
    except SystemExit:
        raise
    except Exception as e:
        logger.error(f"disk_monitor аварийно завершился: {e}")
        raise SystemExit(1)


if __name__ == "__main__":
    # Для запуска: python -m vibe.infra.disk_monitor
    # Аргументы CLI (например --dry-run) обрабатываются в run().
    run()

