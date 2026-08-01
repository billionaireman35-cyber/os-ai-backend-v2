import typing

_orig_evaluate = typing.ForwardRef._evaluate

def _new_evaluate(self, globalns, localns, type_params, recursive_guard=None):
    return _orig_evaluate(self, globalns, localns, type_params, recursive_guard=recursive_guard)

typing.ForwardRef._evaluate = _new_evaluate
