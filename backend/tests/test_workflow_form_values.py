"""Schema value boundary tests shared by HTTP and MCP task submission."""
from flowhub_api.services.workflow import is_empty_form_value


def test_empty_form_value_treats_empty_image_reference_array_as_missing():
    assert is_empty_form_value([])
    assert is_empty_form_value({})
    assert is_empty_form_value("  ")
    assert not is_empty_form_value([{"id": "image-1", "name": "现场图.png"}])
