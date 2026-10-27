import json
import re

import pytest

from opensweep.core import rules as rules_mod
from opensweep.core.platform import resource_path


def test_builtin_rules_are_valid():
    all_files = sorted(resource_path("rules").glob("*.json"))
    assert all_files
    ids = []
    for path in all_files:
        loaded, errors = rules_mod.load_file(path)
        assert errors == [], errors
        ids.extend(r.id for r in loaded)
    assert len(ids) == len(set(ids)), "duplicate rule ids"


def test_every_builtin_rule_has_chinese_name():
    for path in sorted(resource_path("rules").glob("*.json")):
        for rule in rules_mod.load_file(path)[0]:
            assert "zh_CN" in rule.name, rule.id
            assert rule.description.get("en"), rule.id


@pytest.mark.parametrize("bad_path", ["~", "%USERPROFILE%", "C:\\", "/", "*", "$HOME"])
def test_validate_rejects_broad_paths(bad_path):
    data = {"id": "x", "category": "system", "name": {"en": "x"}, "paths": {"windows": [bad_path]}}
    assert any("too broad" in e for e in rules_mod.validate(data))


def test_validate_rejects_unknown_keys_and_values():
    data = {"id": "x", "category": "nope", "name": {"en": "x"}, "paths": {"windows": ["C:\\x\\y"]},
            "risk": "yolo", "dangerous": True}
    errors = " ".join(rules_mod.validate(data))
    assert "bad category" in errors and "bad risk" in errors and "unknown key" in errors


def test_expand_unset_variable_is_skipped(monkeypatch):
    monkeypatch.delenv("OPENSWEEP_UNSET_VAR", raising=False)
    assert rules_mod.expand("%OPENSWEEP_UNSET_VAR%\\hub") is None
    assert rules_mod.expand("$OPENSWEEP_UNSET_VAR/hub") is None


def test_localized_fallbacks():
    names = {"en": "Cache", "zh_CN": "缓存"}
    assert rules_mod.localized(names, "zh_CN") == "缓存"
    assert rules_mod.localized(names, "zh-CN") == "缓存"
    assert rules_mod.localized(names, "fr") == "Cache"


def _locale(code):
    return json.loads(resource_path("locales", f"{code}.json").read_text(encoding="utf-8"))


def test_all_locales_match_english_keys_and_placeholders():
    en = _locale("en")
    for path in sorted(resource_path("locales").glob("*.json")):
        data = _locale(path.stem)
        missing = set(en) - set(data)
        extra = set(data) - set(en)
        assert not missing, f"{path.name} missing {sorted(missing)}"
        assert not extra, f"{path.name} has unknown keys {sorted(extra)}"
        for key, text in en.items():
            want = set(re.findall(r"\{(\w+)\}", text))
            got = set(re.findall(r"\{(\w+)\}", data[key]))
            assert want == got, f"{path.name}:{key} placeholders {got} != {want}"


def test_every_string_used_in_code_exists():
    """Catch t("some.key") calls whose key is missing from en.json."""
    en = _locale("en")
    src = resource_path("rules").parent
    missing = set()
    for path in src.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for key in re.findall(r"""\bt\(\s*["']([a-z_]+\.[a-zA-Z0-9_.]+)["']""", text):
            if key.endswith("_"):  # t("disk.k_" + id): a prefix
                if not any(k.startswith(key) for k in en):
                    missing.add(f"{path.name}: {key}*")
            elif key not in en:
                missing.add(f"{path.name}: {key}")
        # keys built from a prefix, e.g. t(f"startup.why_{kind}") - check the prefix exists at all
        for prefix in re.findall(r"""\bt\(\s*f["']([a-z_]+\.[a-zA-Z0-9_]*)\{""", text):
            if not any(k.startswith(prefix) for k in en):
                missing.add(f"{path.name}: {prefix}*")
    assert not missing, sorted(missing)


def test_translation_function(monkeypatch):
    from opensweep import i18n
    i18n.set_language("zh_CN")
    assert i18n.t("done.freed", size="1 GB") == "已释放 1 GB。"
    assert i18n.t("does.not.exist") == "does.not.exist"
    i18n.set_language("xx")
    assert i18n.current() == "en"
