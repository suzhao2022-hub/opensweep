from opensweep.core import checkup, software, startup
from opensweep.core.engine import RuleResult, Target
from opensweep.core.memory import AppUsage
from opensweep.core.rules import Rule

GB = 1024 ** 3


def _result(rule_id, size, risk="safe", category="system", blocked=None):
    rule = Rule(id=rule_id, category=category, name={"en": rule_id}, paths={}, risk=risk)
    res = RuleResult(rule, [Target(rule, f"C:\\x\\{rule_id}", rule_id, size, 1)], blocked=blocked)
    return res


def _entry(name, advice="disable", enabled=True, exists=True):
    e = startup.StartupEntry(name, name, "", "HKCU", "Run", enabled, exists=exists)
    e.advice = advice
    return e


def test_clean_pc_scores_high():
    facts = checkup.SystemFacts([_entry("ctfmon", "keep")], [], [], [("C:\\", 200 * GB, 500 * GB)], 40)
    findings = checkup.analyze([_result("temp", 100 * 1024 ** 2)], facts)
    assert {f.level for f in findings} == {checkup.OK}
    assert checkup.score(findings) == 100
    assert checkup.grade(100) == "good"


def test_messy_pc_lists_fixes_first():
    old = software.Program(key="k", hive="HKCU", name="Old", size=GB)
    old.last_used = 1.0
    facts = checkup.SystemFacts(
        [_entry("steam"), _entry("thunder"), _entry("gone", exists=False), _entry("ime", "keep")],
        [old],
        [AppUsage("helper.exe", memory=GB, has_window=False, exe="D:\\x\\helper.exe")],
        [("C:\\", 5 * GB, 256 * GB), ("D:\\", 400 * GB, 1000 * GB)],
        90)
    results = [_result("temp", 3 * GB), _result("models", 5 * GB, "review", "ai"),
               _result("chrome", GB, blocked="running")]
    findings = checkup.analyze(results, facts)
    ids = [f.id for f in findings]
    assert ids[:2] == ["junk", "disk:C:\\"] or ids[:2] == ["disk:C:\\", "junk"]
    by_id = {f.id: f for f in findings}
    assert by_id["junk"].size == 3 * GB and by_id["junk"].extra["blocked"] == 1
    assert by_id["startup"].count == 2 and by_id["startup_broken"].count == 1
    assert by_id["ai"].level == checkup.CHECK
    assert by_id["unused"].count == 1
    assert by_id["memory"].level == checkup.CHECK
    assert "disk:D:\\" not in by_id
    assert checkup.score(findings) < 60


def test_startup_classification():
    assert startup.classify("BaiduYunGuanjia", "BaiduNetdisk.exe") == ("cloud", "disable")
    assert startup.classify("ctfmon", "ctfmon.exe") == ("system", "keep")
    assert startup.classify("SogouExplorer", "SogouExplorer.exe")[0] == "browser"
    assert startup.classify("Sogou Pinyin", "SogouImeBroker.exe")[0] == "input"
    assert startup.classify("QQ", "QQ.exe") == ("chat", "optional")
    assert startup.classify("Timer helper", "timer.exe") == ("", "optional")  # "=tim" needs a whole word
    assert startup.classify("Thing", "thing.exe", "Updater for Thing")[0] == "updater"


def test_launched_script_is_found():
    assert startup._launched_file(r'wscript.exe "C:\tools\run me.vbs" /x') == r"C:\tools\run me.vbs"
    assert startup._launched_file(r"rundll32.exe C:\x\y.dll,Start") == r"C:\x\y.dll"
    assert startup._launched_file("cmd.exe /c echo hi") == ""
