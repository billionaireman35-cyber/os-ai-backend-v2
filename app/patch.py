import pydantic
import sys
import typing

if pydantic.VERSION.startswith("1."):
    _orig_evaluate = typing.ForwardRef._evaluate

    if sys.version_info >= (3, 13):
        def _new_evaluate(self, globalns, localns, type_params, recursive_guard=frozenset()):
            return _orig_evaluate(self, globalns, localns, recursive_guard=recursive_guard)
    else:
        def _new_evaluate(self, *args, **kwargs):
            return _orig_evaluate(self, *args, **kwargs)

    typing.ForwardRef._evaluate = _new_evaluate
