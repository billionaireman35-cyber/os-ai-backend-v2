import pydantic
import sys
import typing
import inspect

# parsimonious (a transitive dependency of eth-abi, itself required by
# web3==5.31.3) calls the long-removed inspect.getargspec, which was
# deleted in Python 3.11. Upgrading parsimonious directly conflicts with
# eth-abi==2.2.0's pin (parsimonious<0.9.0), and upgrading eth-abi/web3
# risks breaking existing wallet/swap code - so instead, restore a
# getargspec-shaped wrapper around the modern getfullargspec, matching
# the old function's return shape closely enough for parsimonious's use.
if not hasattr(inspect, "getargspec"):
    def _getargspec(func):
        full = inspect.getfullargspec(func)
        return inspect.ArgSpec(full.args, full.varargs, full.varkw, full.defaults)
    inspect.getargspec = _getargspec

if pydantic.VERSION.startswith("1."):
    _orig_evaluate = typing.ForwardRef._evaluate

    if sys.version_info >= (3, 13):
        def _new_evaluate(self, globalns, localns, type_params, recursive_guard=frozenset()):
            return _orig_evaluate(self, globalns, localns, recursive_guard=recursive_guard)
    else:
        def _new_evaluate(self, *args, **kwargs):
            return _orig_evaluate(self, *args, **kwargs)

    typing.ForwardRef._evaluate = _new_evaluate
