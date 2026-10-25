# The recorder already installed the plugins: show the inventory, the clocks, then the same as JSON into jq.
Require jq

Type "hz system plugin list"
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
