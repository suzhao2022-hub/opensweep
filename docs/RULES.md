# Writing rules

OpenSweep's knowledge lives in JSON files, so you can teach it about a new app without touching Python.

| File | What it describes |
|---|---|
| `src/opensweep/rules/*.json` | What can be cleaned (junk files, caches, models) |
| `src/opensweep/data/startup_advice.json` | Advice for startup apps, tasks and services |
| `src/opensweep/data/known_paths.json` | Plain-language notes for well-known folders in the disk map |

User rules can also go in `%APPDATA%\OpenSweep\rules\*.json` (Settings → *Open custom rules folder*); a user rule with
the same `id` replaces the built-in one.

## Cleanup rules

```json
{
  "id": "someapp-cache",
  "category": "apps",
  "name": {"en": "SomeApp cache", "zh_CN": "SomeApp 缓存"},
  "description": {"en": "Cached images. Logins are kept. Close SomeApp first.", "zh_CN": "…"},
  "paths": {"windows": ["%LOCALAPPDATA%\\SomeApp\\Cache"]},
  "close_apps": ["SomeApp.exe"]
}
```

| Key | Meaning |
|---|---|
| `id` | Unique, lowercase with dashes. |
| `category` | `system`, `browser`, `ai`, `dev` or `apps`. |
| `name`, `description` | Per language; `en` is required. Write the description for someone who knows nothing about computers: what it is and what happens if it's deleted. |
| `paths` | Per platform (`windows`, `linux`, `darwin`). Env vars (`%APPDATA%`, `$HOME`), `~` and `*` wildcards work. A path with an unset variable is skipped. Paths that are too broad (`~`, a drive root…) are rejected. |
| `risk` | `safe` (default): recreated automatically, cleaned by one-click. `review`: the user decides; never cleaned automatically; goes to the Recycle Bin. |
| `contents_only` | Default `true`: empty the folder but keep it. `false`: delete the folder/file itself. |
| `itemize` | `1`–`3`: list each sub-folder at that depth as its own item (used for AI models). |
| `item_patterns` | With `itemize`: only children matching these wildcards. |
| `min_age_days` | Only delete files older than this. |
| `requires_admin` | Needs administrator rights. |
| `close_apps` | Skip the rule while any of these processes run. |
| `action` | `delete` (default) or `report` (show the size only, e.g. virtual disks). |
| `provider` | Special cleaners: `recycle_bin`, `ollama`, `chromium` (any Chromium/Electron app's standard caches). |
| `fallback` | Only take what no other rule already covers. |

**Before sending a rule, check:** the folder really is recreated by the app, logins/chats/settings are not in it, and
the rule only matches that folder. `python -m opensweep rules` validates every rule file.

## Startup advice

```json
{"kind": "cloud", "match": ["baidunetdisk", "=115"]}
```
`match` is compared with the entry name, exe name, exe description and publisher (letters and digits only, lowercase).
A pattern starting with `=` must match a whole word. The first matching group wins, so put specific groups first.
Each `kind` needs `startup.why_<kind>` and `startup.kind_<kind>` texts in the locale files.

## Folder explanations

```json
{"id": "pagefile", "match": ["%SystemDrive%\\pagefile.sys"], "safety": "careful"}
```
`safety`: `keep` (don't touch), `careful` (only through the app or Windows setting named in the text), `user` (the user's
own files), `clean` (OpenSweep can clean it; add `"page": "clean"` to link there). The text is `disk.k_<id>` in the
locale files.

## Translations

Strings are in `src/opensweep/locales/<code>.json`. The tests check every language has every key with the same
`{placeholders}`. To add a language, copy `en.json`, translate the values and set `_language` to the language's own name.
