# Config

The server reads three YAML files. `config.yml` only points at the other two. `hot.yml` can change while people are connected. `cold.yml` is read when the process starts, including after a reboot.

Player-facing meanings of the current keys are in the site's Getting Started page. This page is how to add a key.

## The Three Files

| File         | Set                    | When it is read                                      |
| ------------ | ---------------------- | ---------------------------------------------------- |
| `config.yml` | `WRAPPER_KEYS`         | Startup. Only `hot` and `cold` are allowed.          |
| `hot.yml`    | `HOT_KEYS`             | Startup, `reload config`, and the five-minute check. |
| `cold.yml`   | `COLD_KEYS`            | Startup and the next process after a reboot.         |

`HOT_KEYS`, `COLD_KEYS`, and `WRAPPER_KEYS` are the frozensets at the top of `config.py`. A name has to be in the right set before the loader will keep it.

## Add a Hot Key

Hot is the right place when an admin should be able to change the value without kicking everyone off. `allow_newbies` is the pattern: a bool with a default, replaced by `install_hot`.

1. Add the name to `HOT_KEYS`. Keep that set alphabetical, the same way the existing names are.
2. Add a field to `HotConfig` and to `Config` if command code will read `server.config`.
3. Read the value in `hot_fields`. Give an optional key a default. Raise `ConfigError` with a sentence a human can act on when the value is the wrong type.
4. Copy the field inside `install_hot` in `timer.py`. `reload config` and the five-minute check both call `reload_hot_config`, which calls `install_hot`. If you skip this step, the file changes and the running server does not.
5. If the live server also keeps a copy on `ChatServer` itself, assign that too. `allow_newbies` is copied onto both `server.config` and `server.allow_newbies`.
6. Put a commented example in `config/config/hot.yml`.
7. Add a row to the Hot tables in `mkdocs/getting-started.md` and `mkdocs/reference.md`. Keep those rows alphabetical.
8. Add a test in `tests/test_config.py` for the happy value, the bad type, and a reload that actually changes `server.config`.

A key that only matters at process start belongs in cold, even if it would be convenient to reload. Ports, the rooms path, and `background` are cold for that reason.

## Add a Cold Key

Cold follows the same first steps, with different names.

1. Add the name to `COLD_KEYS`, alphabetical.
2. Add a field to `ColdConfig` and to `Config`.
3. Read it in `cold_fields`. A relative path is joined to the cold file's directory, not the wrapper's directory.
4. Pass the field into the `Config(...)` call in `load_config`.
5. Do not add it to `install_hot`. A reboot starts a new process and reads `cold.yml` again. Open sockets are kept. The new value applies to that new process.
6. Document it on the Cold tables, alphabetical, and cover it in `tests/test_config.py`.

## A Key in the Wrong File

| You put it…                         | What happens                                                                 |
| ----------------------------------- | ---------------------------------------------------------------------------- |
| A hot key in `config.yml`           | Startup stops. The error says it belongs in the hot config.                  |
| A cold key in `hot.yml`             | Startup stops. The error says it belongs in the cold config.                 |
| A name in neither set               | Startup continues. The value is ignored. The name is stored as obsolete.     |

On login, a Site Admin is told the obsolete names and which file they are in. Every connected Site Admin hears that again after a reboot. The lines come from `issue_lines` in `entry.py`.

## Paths

`hot` and `cold` in the wrapper are relative to the wrapper's directory. A path inside `hot.yml` is relative to `hot.yml`. A path inside `cold.yml` is relative to `cold.yml`. `../` walks up from that file.
