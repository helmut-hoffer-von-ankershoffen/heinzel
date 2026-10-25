# Durable turn engine

The first engine slice implements one bounded Pydantic turn with typed output and per-node checkpoints
([ADR 0050](adr/0050-pydantic-ai-runs-a-turn-a-coding-agent-is-a-handoff.md),
[ADR 0067](adr/0067-hz-owns-sessions-across-pydantic-and-native-cli-turns.md)). It is an internal foundation: the
session application service supplies durable storage and effective grants. This slice alone adds no chat command,
schedule, external write, native CLI route or subscription bridge.

`run_turn` accepts an already policy-checked `Model`, instructions, turn identity, limits, optional saved
`Conversation` and a checkpoint sink. It builds a fresh `Agent`, executes the graph one node at a time, and calls
the sink after each completed node. Checkpoints include their format and engine pin, context hash, node and step,
and terminal typed status and outcome. Model messages and usage serialize through the pinned
`ConversationTypeAdapter`; they belong in a private turn body. The spine stores metadata and its body hash.

The sink must save and fsync the body, then append the corresponding checkpoint before returning. A sink failure
stops the turn and escapes to the caller. It never becomes a successful or ordinary model-failure result. Before
resuming, the service must verify the body against the saved hash. The engine does not guess that a missing or
altered body is safe to replay.

Resume uses an explicitly validated `EngineCheckpoint`, including its terminal result; a bare Conversation is
history, not proof that a graph step completed. A crash after the final checkpoint therefore returns the saved
outcome without a second model call, even when the caller never saved a separate result. A changed identity,
instructions, contract context, model or limit refuses that continuation. Starting a new turn keeps portable visible
messages but resets its usage baseline; resuming the same turn preserves usage. This is not a general exactly-once
graph or external-effect guarantee. Adding executable tools requires the replay policy and effect ledger before
claiming recovery across their boundaries.

The closed `Outcome` contains a short report, optional draft, lessons, proposed contract amendment, question and
summary. Unknown fields and coercion of truthy numbers into postconditions are refused. A draft names its contract
version and every checked postcondition. A mismatched version fails; an unmet postcondition skips placement. A
question ends the turn in `waiting`, with no live model process. Neither a draft nor an amendment authorizes an
effect: the application service still checks the approved contract and uses its proposal and effect gate.

Request, output-token, total-token and time limits are mandatory. A cost bound also requires an adapter that reports
priced usage; adapter policy must refuse a hard monetary limit it cannot enforce. The engine preserves the response
and billed usage when the library raises a token or cost limit before completing its normal response append.
Request counts and usage travel in `Conversation` across continuation. Model validation retries consume the same
limits. An unpriced response under a requested cost bound ends `budget`, never `completed`, and retained unpriced
usage refuses a capped continuation before another request. Preflight pricing still belongs to the adapter: the
engine cannot know an omitted response price before that response arrives. A timeout saves completed state;
external task cancellation saves its available snapshot and re-raises the
cancellation so task groups and shutdown retain their semantics.

The shared session models describe one stable hz-owned identity, message pagination, durable inbox requests and
acknowledgements, question revisions, versioned contract artifacts, exact action previews and personal appearance.
Heads must use one application service, never make their own transcript or infer recipients from role labels.
Unspecified personal hat, shirt and beard values preserve the installed palette and age-derived defaults.
Approval proofs are private execution inputs, absent from snapshots and renderer blocks.

The tests use `FunctionModel`, with model requests globally disabled. They cover typed retries, postconditions,
checkpoints, exact serialization, caps across continuation, timeout, external cancellation and checkpoint failures.
No test calls a paid model. The exact `pydantic-ai-slim==2.61.0` pin is deliberate: changing the dependency requires
reviewing and passing these serialization and behavior fixtures.
