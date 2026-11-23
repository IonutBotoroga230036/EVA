# Understanding what you mean (v0.3.1)

Default since Nov 22: **auto** (Claude first, about EUR 0.0001 a command). Status panel > Brain mode > "Understanding you" switches it; Local keeps it on the PC.

Until now one small local model (qwen2.5 3B) chose among all 76 tools at once, and every time it misunderstood,
a word pattern was added in front of it. That pile of patterns is why she felt rigid. This step replaces the
choice itself, and keeps the patterns only as fast shortcuts and as safety rails.

## How a sentence is understood (auto, the default since Nov 22)

1. **Said before?** The same sentence in the same situation reuses its decision, free (see "Cache" below).
2. **Claude Haiku 5.5** decides and fills in the details in one call (a forced tool call, so it answers without
   thinking first: about 0.7 s). Nov 22: Jev chose well, but the small local model filled in details badly
   ("empty slots for tomorrow" became day='morning'), so Claude went first.
3. If Claude is slow, down or out of budget, **Jev** (TypeSafe) picks ONE tool with a real probability, and the
   **local model** fills in that one tool's details. Unsure Jev decisions still pass the word guards.
4. No key, no internet, no budget, or you said "local only": the **local model** decides, as before.

Mode `jev` keeps the original order (Jev first, local details, Claude never).

A sure decision (Jev sure, or Claude) skips the word guards, so "check my calendar for any empty slots" reaches
free time without the word "free". Nothing else is skipped: sending, deleting and spending still ask for your yes.
Skills FORGE builds become choices automatically, from their name and description: no patterns to write.
When the answer is a sure "nothing fits", she offers to build the skill.

## Cache and test mode (Nov 23)

- **Cache** (`core/understand/cache.py`): the decision is kept for 7 days, keyed on the sentence (without "Hey
  Eva", punctuation or case), on the two turns before it for follow-ups ("and tomorrow?", "turn it up"), and on
  the tools she has (a new or changed skill starts fresh). A second VOICE_TESTS run costs nothing for
  understanding. Decisions carrying personal text (a message, a note, a reminder's words) stay in memory only;
  a decision with a written-out date is never reused. "Local only" never reads it.
  Off: `understanding.cache.enabled: false`.
- **Test mode**: "Eva, test mode on" caps cloud spending at 10 cents ("test mode on with 50 cents"); past it,
  everything falls back to local. Every turn logs `COST turn: ...` (free turns too). "Test mode off" says what
  the session cost. Ends at midnight by itself.

## Privacy

Cloud understanding only runs when the "understanding" brain feature resolves to cloud or auto. If you say
"Eva, local only" (or the feature is set to local), nothing leaves the PC. Jev and Claude get the sentence and the
two turns before it, never your files, memories or emails. Jev's listing says zero data retention.

## Setup

1. Keys in `config\secrets.env` (never in chat):
   - `TYPESAFE_API_KEY=...` from console.typesafe.ai (or `OPENROUTER_API_KEY=...` and set
     `understanding.jev.provider: "openrouter"`)
   - `ANTHROPIC_API_KEY=...` (already there; check it with the service check)
2. Optional, a better local model: `ollama pull qwen3.5:4b`, then in `config\settings.local.yaml`:
   ```yaml
   understanding:
     local_model: "qwen3.5:4b"
   ```
3. Mark your own sentences (about 10 minutes, Enter when she was right):
   `python -m core.understand.cases`
4. Score every mode on the seed sentences plus yours. Nothing gets executed:
   `python -m core.understand.eval`
5. Switch to the winner in `config\settings.local.yaml`, and allow cloud for it:
   ```yaml
   understanding:
     mode: "auto"
   inference:
     cloud:
       features:
         understanding: "auto"
   ```
   Or by voice: "Eva, use Claude for understanding" / "Eva, understanding local only".

## Modes

| mode | who chooses | details filled by | word guards |
|---|---|---|---|
| legacy | qwen2.5 3B, all tools at once | same | always |
| local | `local_model`, all tools at once | same | always |
| jev | Jev | local model | only when Jev isn't sure |
| claude | Claude Haiku | Claude | never (Claude is trusted) |
| auto | Claude; Jev if Claude fails; then local | Claude / local | only when Jev is unsure or local decides |

## Costs

Jev is listed at about 4 cents per million input tokens; one decision is a few hundred tokens. Claude Haiku 5.5 is
$0.10 per million input tokens under 100K per prompt, with the tool list cached. Both count against your
existing daily cloud budget (Claude does; Jev is cheap enough that it isn't tracked).
