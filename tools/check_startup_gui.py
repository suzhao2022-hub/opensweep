import os, sys
from pathlib import Path
from PySide6.QtWidgets import QApplication
from opensweep.core import boottrace, startup
from opensweep.gui import boot_dialog, theme
from opensweep.gui.pages.startup import StartupPage
from opensweep.i18n import set_language
from opensweep.settings import Settings

app=QApplication([])
set_language("zh_CN")
report=boottrace.collect()
assert len(report.entries) > 0, "No startup entries read"
out=Path(sys.argv[1]); out.mkdir(parents=True,exist_ok=True)
(out/"source-startup-report.json").write_text(report.to_json(),encoding="utf-8")
boot_dialog.run_task=lambda fn,on_done,**kw:on_done(report)
for skin in theme.THEMES:
    theme.apply(app,skin)
    page=StartupPage(Settings(guard=False))
    page.loaded=True
    page.resize(1320,840)
    page._fill(report.entries)
    page.show()
    app.processEvents()
    assert page.grab().save(str(out/f"{skin}-startup.png"))
    dialog=boot_dialog.BootDialog(page)
    dialog.show()
    app.processEvents()
    assert dialog.grab().save(str(out/f"{skin}-diagnostic.png"))
    dialog.close()
    page.close()
print(f"rendered 3 themes; {len(report.entries)} startup entries; {len(report.items)} visible apps; errors={report.errors}")
