from setuptools import setup

setup(
    app=["CodefreshStatus.py"],
    data_files=["assets/green.png", "assets/red.png"],
    options={
        "py2app": {
            "iconfile": "/Users/mhill/Projects/Personal/cfstatus/assets/CodefreshStatus.icns",
            "plist": {"LSUIElement": True},  # don't show in dock
        }
    },
    setup_requires=["py2app"],
)
