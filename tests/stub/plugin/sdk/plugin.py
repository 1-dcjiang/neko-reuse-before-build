"""最小 N.E.K.O SDK 桩：只为了让插件类能被导入并在本机跑一遍。

它**不是** N.E.K.O 的替代品：装饰器只做透传，不做校验。
真机行为（权限、UI、推送、10 秒就绪窗口）必须在宿主里验证。
"""

import logging


class SdkError(Exception):
    pass


class _Result:
    def __init__(self, value, ok):
        self.value = value
        self.ok = ok


def Ok(value=None):
    return _Result(value, True)


def Err(error=None):
    return _Result(error, False)


def neko_plugin(cls):
    return cls


def _passthrough_decorator(**_kwargs):
    def _wrap(func):
        return func
    return _wrap


lifecycle = _passthrough_decorator
llm_tool = _passthrough_decorator


class _Ui:
    action = staticmethod(_passthrough_decorator)
    context = staticmethod(_passthrough_decorator)


ui = _Ui()


class NekoPluginBase:
    def __init__(self, ctx):
        self.ctx = ctx
        self.config = ctx.config
        self.logger = logging.getLogger("stub.plugin")
        self.last_status = None

    def report_status(self, payload):
        self.last_status = payload

    def data_path(self, *parts):
        import os
        base = os.path.join(os.path.expanduser("~"), "N.E.K.O", "stub")
        path = os.path.join(base, *[str(p) for p in parts])
        os.makedirs(path if not parts or parts[-1] == "" else os.path.dirname(path), exist_ok=True)
        return path

    def push_message(self, **_):
        return None
