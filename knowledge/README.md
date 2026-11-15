# Knowledge map

The project's map: what each part does, where it lives, why it was built that way, and what we learned.
Start here, follow one link per step, and read only the note you need. Never read whole folders.

## Topics
- [Systems](systems/systems.md): one note per part of the project (what it does, its files, how it connects)
- [Findings](findings/findings.md): one lesson per note (a trap, a fix, something that surprised us)
- [Decisions](decisions/decisions.md): one choice per note (what we picked, what we didn't, and why)

## Rules
- One system, finding or decision per note. Never append a new thing to an existing note: make a new one.
- Name notes `<kind>-<what-it-is>.md` in lowercase (`system-shop.md`, `finding-dates-are-utc.md`):
  names stay unique, so grep and Obsidian find the right one.
- Every note starts with `# Title` and a one-line summary `> ...`. `grep -rh "^> " knowledge` prints the whole map.
- Index notes hold one line per link. When an index passes ~40 lines, split it into topic folders, each with its
  own index note named after the folder (`findings/ui/ui.md`), and link that from here.
- Notes say what the code can't: purpose, connections, reasons. Never copy code into a note: it goes stale.
- Links are normal relative links `[text](path.md)`: they work on GitHub, in grep and in Obsidian.
- New notes start from `templates/notes/`. `python agent/check_docs.py` finds broken links, orphans and missing files.
