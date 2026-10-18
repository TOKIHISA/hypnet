"""hypnet: 仮説と知見のネットワーク（交換フォーマットと、知見の導出・伝搬・来歴・台帳）。

分析の手順には依存しない。どんな分析の道具（あるいは手作業）でも、hypnet の YAML を書き出せば
このネットワークに参加できる。フォーマットは FORMAT.md、JSON Schema は `hn schema`。
"""
from .net import Net
from .schema import KINDS

__version__ = "0.1.0"
__all__ = ["Net", "KINDS"]
