# The README's demo: from install to the first commands. Render with `vhs docs/assets/demo.tape` from the repository
# root, with `hz` from this checkout on PATH (`uv tool install --editable .`) and jq installed; the Demo workflow
# re-renders it on main. Only the install line is faked: everything after it runs the real `hz`.

Output docs/assets/demo.gif

Require hz
Require jq

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
Type "hz system plugin remove heinzel-clock >/dev/null 2>&1; clear"
Enter
Wait@60s
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

Hide
Type "clear"
Enter
Show
Type "hz system plugin add clock"
Enter
Wait@60s
Sleep 1.5s

Type "hz clock"
Enter
Wait
Sleep 2.5s

Hide
Type "clear"
Enter
Show
Type "hz system doctor"
Enter
Wait
Sleep 3s

Hide
Type "clear"
Enter
Show
Type "hz clock --json | jq"
Enter
Wait
Sleep 3s
