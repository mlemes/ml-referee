def test_package_has_version():
    import ml_referee
    assert ml_referee.__version__ == "0.1.0"

def test_manifest_name():
    import json, pathlib
    m = json.loads(pathlib.Path(".claude-plugin/plugin.json").read_text())
    assert m["name"] == "ml-referee"
