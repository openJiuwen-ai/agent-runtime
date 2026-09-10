# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""审计日志配置聚合（FR8 命名空间骨架；FR1 的 format / ntp 已生效）。

默认配置不含任何环境特定地址。加载时走合规校验门，不通过则拒绝生效。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from .clock import NtpConfig
from .schema import AuditLogSchema
from .validator import validate_schema


@dataclass(frozen=True)
class RuntimeIdentityConfig:
    data_center: str = "-"
    system_code: str = "-"
    node_strategy: str = "ip_port"
    node: str = "-"

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> RuntimeIdentityConfig:
        if not data:
            return cls()
        return cls(
            data_center=str(data.get("data_center", "-")),
            system_code=str(data.get("system_code", "-")),
            node_strategy=str(data.get("node_strategy", "ip_port")),
            node=str(data.get("node", "-")),
        )


@dataclass(frozen=True)
class RedactionConfig:
    """FR3 脱敏配置骨架。校验门会拒绝 command_force_redact=False。"""

    redaction_token: str = "***REDACTED***"
    sensitive_keys: tuple[str, ...] = (
        "password",
        "token",
        "secret",
        "key",
        "cvn",
        "pin",
        "cvn2",
        "otp",
        "captcha",
        "smscode",
        "dynamic_code",
        "verify_code",
    )
    command_force_redact: bool = True
    sandbox_path_redact: bool = True
    extra: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> RedactionConfig:
        if not data:
            return cls()
        keys = data.get("sensitive_keys")
        return cls(
            redaction_token=str(data.get("redaction_token", "***REDACTED***")),
            sensitive_keys=tuple(keys) if keys is not None else cls.sensitive_keys,
            command_force_redact=bool(data.get("command_force_redact", True)),
            sandbox_path_redact=bool(data.get("sandbox_path_redact", True)),
            extra=dict(data),
        )


@dataclass(frozen=True)
class WriterConfig:
    """FR4 异步输出配置骨架。"""

    async_write: bool = True
    queue_size: int = 10000
    overflow_policy: str = "spill_to_disk"
    max_line_size: int = 524288
    truncation_tag: str = "...[TRUNCATED，原长{len}字节]"
    encoding: str = "utf-8"
    audit_enabled: bool = True
    disable_requires_auth: bool = True
    debug_in_prod: bool = False

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> WriterConfig:
        if not data:
            return cls()
        async_cfg = data.get("async") or {}
        switch = data.get("audit_switch") or {}
        level_filter = data.get("level_filter") or {}
        return cls(
            async_write=bool(data.get("async_write", True)),
            queue_size=int(async_cfg.get("queue_size", 10000)),
            overflow_policy=str(async_cfg.get("overflow_policy", "spill_to_disk")),
            max_line_size=int(data.get("max_line_size", 524288)),
            truncation_tag=str(data.get("truncation_tag", "...[TRUNCATED，原长{len}字节]")),
            encoding=str(data.get("encoding", "utf-8")),
            audit_enabled=bool(switch.get("enabled", True)),
            disable_requires_auth=bool(switch.get("disable_requires_auth", True)),
            debug_in_prod=bool(level_filter.get("debug_in_prod", False)),
        )


@dataclass(frozen=True)
class StorageConfig:
    """FR5 存储 / 轮转 / 留存配置骨架。"""

    filename_pattern: str = "SEC-{dc}_{sys}_{node}.log"
    filename_charset: str = r"[A-Za-z0-9_]"
    filename_max_length: int = 64
    storage_dir: str = "/var/log/audit/"
    storage_separation: bool = True
    integrity_protection: bool = True
    rotation: Mapping[str, Any] = field(
        default_factory=lambda: {
            "mode": "size_and_time",
            "max_bytes": 20971520,
            "backup_count": 20,
            "rollover_suffix": ".log.{n}",
            "expiry_handler": "secure_delete",
        }
    )
    retention: Mapping[str, Any] = field(
        default_factory=lambda: {
            "data_level_field": "data_level",
            "mapping": {
                "普通": "6m",
                "level_3": "1y",
                "level_4_5": "3y",
                "personal_info_outbound": "3y",
            },
            "protect_before_expiry": True,
            "expiry_action": "secure_delete",
        }
    )

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> StorageConfig:
        if not data:
            return cls()
        rotation = dict(cls().rotation)
        rotation.update(data.get("rotation") or {})
        retention = dict(cls().retention)
        incoming_ret = data.get("retention") or {}
        retention.update(incoming_ret)
        if "mapping" in incoming_ret:
            retention["mapping"] = dict(incoming_ret["mapping"])
        return cls(
            filename_pattern=str(data.get("filename_pattern", cls.filename_pattern)),
            filename_charset=str(data.get("filename_charset", cls.filename_charset)),
            filename_max_length=int(data.get("filename_max_length", 64)),
            storage_dir=str(data.get("storage_dir", cls.storage_dir)),
            storage_separation=bool(data.get("storage_separation", True)),
            integrity_protection=bool(data.get("integrity_protection", True)),
            rotation=rotation,
            retention=retention,
        )


@dataclass(frozen=True)
class ShipperConfig:
    """FR6 采集外送配置骨架。endpoint 禁止硬编码。"""

    protocol: str = ""
    endpoint: str = ""
    auth: str = ""
    field_mapping: Mapping[str, str] = field(default_factory=dict)
    extra: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> ShipperConfig:
        if not data:
            return cls()
        shipper = data.get("shipper") if "protocol" not in data else data
        shipper = shipper or {}
        return cls(
            protocol=str(shipper.get("protocol", "")),
            endpoint=str(shipper.get("endpoint", "")),
            auth=str(shipper.get("auth", "")),
            field_mapping=dict(shipper.get("field_mapping") or {}),
            extra=dict(shipper),
        )


@dataclass(frozen=True)
class AccessConfig:
    """FR7 访问控制配置骨架。"""

    roles: Mapping[str, str] = field(
        default_factory=lambda: {
            "business_account": "none",
            "audit_admin": "read",
            "log_writer": "append_only",
        }
    )
    log_writer_mode: str = "append_only"
    require_auth: bool = True
    extra: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> AccessConfig:
        if not data:
            return cls()
        access = data.get("access") if "roles" not in data else data
        access = access or {}
        roles = dict(cls().roles)
        roles.update(access.get("roles") or {})
        return cls(
            roles=roles,
            log_writer_mode=str(access.get("log_writer_mode", "append_only")),
            require_auth=bool(access.get("require_auth", True)),
            extra=dict(access),
        )


@dataclass(frozen=True)
class DeployConfig:
    """FR8 配置下发 / 滚动升级骨架。"""

    page_requires_auth: bool = True
    precheck_gate: bool = True
    extra: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> DeployConfig:
        if not data:
            return cls()
        return cls(
            page_requires_auth=bool(data.get("page_requires_auth", True)),
            precheck_gate=bool(data.get("precheck_gate", True)),
            extra=dict(data),
        )


@dataclass(frozen=True)
class LinkpointConfig:
    """FR2 全链路打点对照表骨架。"""

    submdl_names: Mapping[str, str] = field(
        default_factory=lambda: {
            "gateway": "gateway",
            "agent": "agent",
            "api_client": "api_client",
            "file": "file",
            "sandbox": "sandbox",
            "alert": "alert",
        }
    )
    sandbox_submdl: str = "sandbox"
    extra: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> LinkpointConfig:
        if not data:
            return cls()
        names = dict(cls().submdl_names)
        names.update(data.get("submdl_names") or {})
        return cls(
            submdl_names=names,
            sandbox_submdl=str(data.get("sandbox_submdl", "sandbox")),
            extra=dict(data),
        )


@dataclass(frozen=True)
class AuditLogConfig:
    """完整审计配置。当前真正消费 format / ntp / identity / redaction 开关。"""

    format: AuditLogSchema = field(default_factory=AuditLogSchema)
    ntp: NtpConfig = field(default_factory=NtpConfig)
    identity: RuntimeIdentityConfig = field(default_factory=RuntimeIdentityConfig)
    redaction: RedactionConfig = field(default_factory=RedactionConfig)
    writer: WriterConfig = field(default_factory=WriterConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    shipper: ShipperConfig = field(default_factory=ShipperConfig)
    access: AccessConfig = field(default_factory=AccessConfig)
    deploy: DeployConfig = field(default_factory=DeployConfig)
    linkpoint: LinkpointConfig = field(default_factory=LinkpointConfig)

    @classmethod
    def from_dict(
        cls,
        data: Mapping[str, Any] | None,
        *,
        validate: bool = True,
    ) -> AuditLogConfig:
        data = dict(data or {})
        fmt = data.get("format") or data
        # 允许直接传入 SRS 的 AUDIT_LOG_FORMAT 片段。
        if "header" in data and "format" not in data:
            fmt = data
        identity_data = {
            "data_center": data.get("data_center", "-"),
            "system_code": data.get("system_code", "-"),
            "node_strategy": data.get("node_strategy", "ip_port"),
            "node": data.get("node", "-"),
        }
        cfg = cls(
            format=AuditLogSchema.from_dict(fmt if isinstance(fmt, Mapping) else None),
            ntp=NtpConfig.from_dict(data.get("ntp")),
            identity=RuntimeIdentityConfig.from_dict(identity_data),
            redaction=RedactionConfig.from_dict(data),
            writer=WriterConfig.from_dict(data),
            storage=StorageConfig.from_dict(data),
            shipper=ShipperConfig.from_dict(data),
            access=AccessConfig.from_dict(data),
            deploy=DeployConfig.from_dict(data.get("deploy")),
            linkpoint=LinkpointConfig.from_dict(data),
        )
        if validate:
            cfg.validate()
        return cfg

    def validate(self) -> None:
        validate_schema(
            self.format,
            ntp=self.ntp,
            command_force_redact=self.redaction.command_force_redact,
        )


def default_config(*, validate: bool = True) -> AuditLogConfig:
    cfg = AuditLogConfig()
    if validate:
        cfg.validate()
    return cfg
