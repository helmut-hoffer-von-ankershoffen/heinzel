# The core's part of the README demo: settings, a quiet prompt, the version and system info. Modules add
# their own part in a demo.tape next to their __init__.py; scripts/demo_tape.py assembles docs/assets/demo.tape from
# this file and those snippets (`make fix`), and `make demo` renders it after installing the isolated environment.

Output docs/assets/demo.gif

Require hz

Set Shell bash
Set Theme "Catppuccin Mocha"
Set FontSize 16
Set Width 1200
Set Height 720
Set Padding 20
Set Framerate 12
Set TypingSpeed 35ms
Set PlaybackSpeed 1.0
Set WaitPattern /\$\s*$/

Hide
# A quiet prompt; installation has already completed in the isolated recording environment.
Type `export PS1='$ '`
Enter
Type "clear"
Enter
Show

Type "hz --version"
Enter
Wait
Sleep 1.5s

Hide
Type "clear"
Enter
Show
Type "hz system info"
Enter
Wait
Sleep 3s
