# The core's part of the README demo: settings, a quiet prompt, the install, the version and system info. Modules add
# their own part in a demo.tape next to their __init__.py; scripts/demo_tape.py assembles docs/assets/demo.tape from
# this file and those snippets (`make fix`), and `make demo` renders it. Only the install line is faked.

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
# A quiet prompt; the install is already done, so print what uv prints instead of reinstalling.
Type `export PS1='$ '`
Enter
Type `uv() { if [ "$1 $2" = "tool install" ]; then sleep 1; printf 'Resolved 64 packages in 812ms\nInstalled 64 packages in 95ms\n + heinzel==%s\nInstalled 1 executable: hz\n' "$(hz --version | head -1 | cut -d' ' -f2)"; else command uv "$@"; fi; }`
Enter
Type "clear"
Enter
Show

Type "uv tool install git+https://github.com/helmut-hoffer-von-ankershoffen/heinzel"
Sleep 300ms
Enter
Wait
Sleep 1.5s

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
