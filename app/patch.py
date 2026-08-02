import pydantic
import sys
import typing

# Only apply the ForwardRef patch if we're on pydantic 1.x
# Pydantic 2.x handles ForwardRef correctly on Python 3.12+.
if pydantic.VERSION.startswith("1."):
    _orig_evaluate = typing.ForwardRef._evaluate

    def _new_evaluate(self, globalns, localns, recursive_guard=frozenset()):
        # Python 3.12.0 ForwardRef._evaluate signature: 
        # (self, globalns, localns, recursive_guard=frozenset())
        # The original patch passed type_params which doesn't exist in 3.12.0.
        # We drop it and forward correctly.
        return _orig_evaluate(self, globalns, localns, recursive_guard=recursive_guard)

    typing.ForwardRef._evaluate = _new_evaluate
