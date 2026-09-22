"""
配置加载器是什么？为什么需要它？解决什么问题？
❌ 没有配置加载器时的困境：
硬编码配置
├── 问题1：修改配置需要改代码，容易出错
├── 问题2：配置分散在多个文件，难以管理
└── 问题3：缺少类型检查，运行时才发现错误

✅ 有了配置加载器后的优势：
统一配置管理
├── 优势1：配置与代码分离，修改更安全
├── 优势2：配置集中管理，易于维护
└── 优势3：自动类型检查，提前发现错误
"""

#  导入依赖
from pathlib import Path
from typing import Type, TypeVar, Union
#  导入OmegaConf（配置管理库）
from omegaconf import OmegaConf, DictConfig

#  定义泛型类型变量
T = TypeVar("T")


def load_config(schema_cls: Type[T], config_file: Path) -> T:
    # 1. 创建 schema（用于校验）
    schema = OmegaConf.structured(schema_cls)

    # 2. 加载内容
    content = OmegaConf.load(config_file)

    # 3. 合并 + 校验
    conf = OmegaConf.merge(schema, content)

    # 4. 转为 dataclass 对象
    return OmegaConf.to_object(conf)
