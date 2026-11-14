"""The lines every fake Claude Code CLI starts with: a claude.ai login, a reviewed release and its option contract."""

from heinzel.kernel._claude_code import CONTRACT_OPTIONS

CLAUDE_PRELUDE = f"""if sys.argv[1:] == ['auth','status','--json']:
 print(json.dumps({{'loggedIn':True,'authMethod':'claude.ai','apiProvider':'firstParty'}})); raise SystemExit
if sys.argv[1:] == ['--version']:
 print('2.1.312 (Claude Code)'); raise SystemExit
if sys.argv[1:] == ['--help']:
 print({" ".join(CONTRACT_OPTIONS)!r}); raise SystemExit"""
"""Paste as one line of a fake's source; the fake imports json and sys first."""
