# The clock plugin in the README demo: add it, show the clocks, then the same as JSON into jq.
Require jq

Hide
Type "hz system plugin remove heinzel-clock >/dev/null 2>&1; clear"
Enter
Wait@60s
Show
Type "hz system plugin add clock --confirm"
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
Type "hz clock --json | jq"
Enter
Wait
Sleep 3s
