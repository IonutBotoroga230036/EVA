# Understanding what you mean (v0.3.1)

Default since Nov 22: **auto** (Claude first, about EUR 0.0001 a command). Status panel > Brain mode > "Understanding you" switches it; Local keeps it on the PC.

Until now one small local model (qwen2.5 3B) chose among all 76 tools at once, and every time it misunderstood,
a word pattern was added in front of it. That pile of patterns is why she felt rigid. This step replaces the
choice itself, and keeps the patterns only as fast shortcuts and as safety rails.

## How a sentence is understood

1. **Jev** (TypeSafe) reads the sentence and the two turns before it and picks ONE of her tools, or "no tool",
   or "nothing fits". It answers in a fraction of a second with a real probability.
2. If Jev is sure (0.8 or more, `understanding.jev.sure`), the **local model** fills in the details for that one
   tool only: the day, the playlist, the person. One tool's details is an easy job even for a small model.
3. If Jev isn't sure, **Claude Haiku 5.5** decides and fills in the details in one call (a forced tool call, so it
   answers without thinking first: about a second).
4. No key, no internet, no budget, or you said "local only": the **local model** decides, as before.

A sure decision (Jev sure, or Claude) skips the word guards, so "check my calendar for any empty slots" reaches
free time without the word "free". Nothing else is skipped: sending, deleting and spending still ask for your yes.
Skills FORGE builds become choices automatically, from their name and description: no patterns to write.
When the answer is a sure "nothing fits", she offers to build the skill.

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
| auto | Jev, then Claude if unsure | local / Claude | only for the local fallback |

## Costs

Jev is listed at about 4 cents per million input tokens; one decision is a few hundred tokens. Claude Haiku 5.5 is
$0.10 per million input tokens under 100K per prompt, with the tool list cached. Both count against your
existing daily cloud budget (Claude does; Jev is cheap enough that it isn't tracked).
