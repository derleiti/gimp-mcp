import pytest
from gimp_mcp.operations import GimpOperations
from gimp_mcp.errors import GimpMcpError


def test_translate_dict_normalizes():
    assert GimpOperations._normalize_transform_values('translate', {'x': 30, 'y': -5}) == [30.0, -5.0]
    assert GimpOperations._normalize_transform_values('translate', {'dx': 3, 'dy': 4}) == [3.0, 4.0]


def test_rotate_and_scale_dict_normalize():
    assert GimpOperations._normalize_transform_values('rotate', {'degrees': 45}) == [45.0]
    assert GimpOperations._normalize_transform_values('rotate', {'angle': 30, 'cx': 10, 'cy': 20}) == [30.0, 10.0, 20.0]
    assert GimpOperations._normalize_transform_values('scale', {'x': 10, 'y': 20, 'width': 100, 'height': 50}) == [10.0, 20.0, 110.0, 70.0]


def test_bad_transform_values_fail_cleanly():
    with pytest.raises(GimpMcpError):
        GimpOperations._normalize_transform_values('translate', {'left': 1})
