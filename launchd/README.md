# launchd

Copies of the LaunchAgent plists installed in `~/Library/LaunchAgents/` on the
Mac mini, kept here so a rebuild doesn't depend on memory. Paths are absolute
for that machine (`/Users/mini/...`).

Restore one:

```bash
cp launchd/<name>.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/<name>.plist
```

If you change the installed plist, copy it back here and commit.
