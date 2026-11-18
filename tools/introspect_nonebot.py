"""内省 NoneBot2 matcher / driver / plugin 相关 API。"""

import inspect

import nonebot
from nonebot import on_command, on_message
from nonebot.matcher import Matcher
from nonebot.plugin import PluginMetadata
from nonebot.rule import Rule

print("nonebot", nonebot.__version__ if hasattr(nonebot, "__version__") else "?")
print("on_message:", inspect.signature(on_message))
print("on_command:", inspect.signature(on_command))
print("Matcher methods:", [m for m in dir(Matcher) if not m.startswith("_")])
print()
print("PluginMetadata fields:", list(PluginMetadata.model_fields.keys()))
print()
driver_cls = nonebot.get_driver
print("get_driver:", inspect.signature(driver_cls))
print("require:", inspect.signature(nonebot.require))
print("get_plugin_config:", inspect.signature(nonebot.get_plugin_config))
print()
print("Rule fields:", [f for f in dir(Rule) if not f.startswith("_")][:20])
print()
print("load_plugin:", inspect.signature(nonebot.load_plugin))
print("load_plugins:", inspect.signature(nonebot.load_plugins))
print()
from nonebot.adapters.onebot.v11 import Bot as V11Bot, GroupMessageEvent
print("v11 Bot send:", inspect.signature(V11Bot.send))
