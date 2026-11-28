# coding: utf-8
"""服务配置契约校验（Manager / Agent Runtime 共用）。

三段式 config_sync 载荷（containers / templates / scopes）的解析与校验只能有
一套实现：Manager 在写库前预检、Runtime 在下发时校验，若各自实现就会出现
「Manager 允许保存、Runtime 整包 400」的不一致（issue #203）。

本包只依赖 foundation 自身，不 import 任何 application 代码。
"""
