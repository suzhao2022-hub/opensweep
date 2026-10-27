import sys

if "--guard" in sys.argv:
    from opensweep.gui.guard_run import main
else:
    from opensweep.gui.app import main

sys.exit(main())
