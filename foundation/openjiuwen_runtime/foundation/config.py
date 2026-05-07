# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""Server Configuration"""
import os
from typing import Optional, Literal
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field, model_validator

# 定义项目根目录
PROJECT_ROOT = Path(__file__).parents[3].resolve()


class Settings(BaseSettings):
    # --------------------------
    # 【基础配置】
    # --------------------------
    DB_TYPE: Literal["mysql", "sqlite", "gaussdb", "opengauss"] = Field(default="sqlite", env="DB_TYPE")

    # --------------------------
    # 【MySQL 配置】（静态可选，DB_TYPE=mysql 时必选）
    # --------------------------
    DB_HOST: Optional[str] = Field(default=None, env="DB_HOST")
    DB_PORT: Optional[int] = Field(default=None, env="DB_PORT")
    DB_USER: Optional[str] = Field(default=None, env="DB_USER")
    DB_PASSWORD: Optional[str] = Field(default=None, env="DB_PASSWORD")
    DB_NAME: Optional[str] = Field(default=None, env="DB_NAME")

    # --------------------------
    # 【服务配置】
    # --------------------------
    IP: Optional[str] = Field(default=None, env="IP")
    LOWCODE_IMAGE: Optional[str] = Field(default=None, env="LOWCODE_IMAGE")

    # 可选配置
    DEPLOY_DIR: str = Field(default="/tmp/deploys", env="DEPLOY_DIR")
    DIST_DIR: str = Field(default="/tmp/dist", env="DIST_DIR")
    HOST: str = Field(default="0.0.0.0", env="HOST")
    PORT: int = Field(default=8186, env="PORT")
    UV_EXTRA_ARGS: str = Field(default="", env="UV_EXTRA_ARGS")
    DEPLOY_TYPE: Literal["subprocess", "docker", "k8s"] = Field(default="subprocess", env="DEPLOY_TYPE")
    MODE: Literal["dev", "product"] = Field(default="product", env="MODE")

    # ========================
    # 内置 对象
    # ========================
    deploy_path: Path | None = None
    dist_path: Path | None = None

    # ========================
    # MySQL 动态必选校验
    # ========================
    @model_validator(mode="after")
    def check_mysql_required(self) -> "Settings":
        if self.DB_TYPE in {"mysql", "gaussdb", "opengauss"}:
            missing = []
            if not self.DB_HOST:
                missing.append("DB_HOST")
            if not self.DB_PORT:
                missing.append("DB_PORT")
            if not self.DB_USER:
                missing.append("DB_USER")
            if not self.DB_PASSWORD:
                missing.append("DB_PASSWORD")
            if not self.DB_NAME:
                missing.append("DB_NAME")

            if missing:
                raise ValueError(
                    f"When DB_TYPE is mysql/gaussdb/opengauss, the following fields are required: {', '.join(missing)}"
                )
        return self

    @model_validator(mode="after")
    def check_runtime_required(self) -> "Settings":
        missing = []
        if not self.IP:
            missing.append("IP")

        if self.DEPLOY_TYPE in {"docker", "k8s"} and not self.LOWCODE_IMAGE:
            missing.append("LOWCODE_IMAGE")

        if missing:
            deploy_type = self.DEPLOY_TYPE
            raise ValueError(
                f"Missing required runtime settings for DEPLOY_TYPE={deploy_type}: {', '.join(missing)}"
            )

        return self

    # ========================
    # 路径校验：相对目录 → 项目根目录绝对路径 + 自动创建
    # ========================
    @model_validator(mode="after")
    def resolve_and_create_paths(self) -> "Settings":
        """
        如果 DEPLOY_DIR / DIST_DIR 是相对路径，自动拼接为项目根目录的绝对路径
        并自动创建目录（避免运行时报错）
        """
        # 处理 DEPLOY_DIR
        self.deploy_path = Path(self.DEPLOY_DIR)
        if not self.deploy_path.is_absolute():
            self.deploy_path = PROJECT_ROOT / self.deploy_path
        # 与 .env 中 DEPLOY_DIR 键名一致，保持大写属性名（Pydantic 字段）
        object.__setattr__(
            self,
            "DEPLOY_DIR",
            str(self.deploy_path.resolve()),
        )

        # 处理 DIST_DIR
        self.dist_path = Path(self.DIST_DIR)
        if not self.dist_path.is_absolute():
            self.dist_path = PROJECT_ROOT / self.dist_path
        object.__setattr__(
            self,
            "DIST_DIR",
            str(self.dist_path.resolve()),
        )

        # 自动创建目录（可选，非常实用）
        self.deploy_path.mkdir(parents=True, exist_ok=True)
        self.dist_path.mkdir(parents=True, exist_ok=True)

        return self


    # 自动从 .env 读取
    model_config = SettingsConfigDict(
        env_file=os.path.join(PROJECT_ROOT, "server/.env"),
        env_file_encoding="utf-8",
        case_sensitive=True
    )

# 初始化配置
settings = Settings()
