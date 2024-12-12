import pytest
from pycrdt import Array, Doc, Map


@pytest.mark.mypy_testing
def mypy_test_array():
    doc = Doc()
    array0: Array[int] = doc.get("array0", type=Array)
    array0.append(0)
    array0.append("foo")  # E: Argument 1 to "append" of "Array" has incompatible type "str"; expected "int"  [arg-type]


@pytest.mark.mypy_testing
def mypy_test_map():
    doc = Doc()
    map0: Map[bool] = doc.get("map0", type=Map)
    map0["foo"] = True
    map0["foo"] = "bar"  # E: Incompatible types in assignment (expression has type "str", target has type "bool")
    v0: str = map0.pop("foo")  # E: Incompatible types in assignment (expression has type "bool", variable has type "str")
    v1: bool = map0.pop("foo")
