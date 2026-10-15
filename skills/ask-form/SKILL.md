---
name: ask-form
description: Ask the user many clarifying questions at once through a browser form instead of the terminal. Use whenever you need more than 4 clarifying questions (AskUserQuestion holds at most 4), or questions that need free-text answers, more than 4 options, or longer context — instead of listing questions in the terminal or spreading them over several AskUserQuestion rounds. Works on Windows, Linux and WSL.
---

# ask-form

Shows all your questions on one local web page (opened in the user's browser), waits until the user submits, and returns the answers to you. No dependencies beyond Python 3.8+.

## When

- More than 4 questions, or any question that does not fit AskUserQuestion → use this.
- 1–4 simple multiple-choice questions → plain AskUserQuestion is still fine.
- Gather **all** open questions into one form. Do not drip them over several rounds and do not also print them in the terminal.

## Steps

1. Write the questions to a JSON file — in the session scratchpad directory if one exists, otherwise a temp dir (not the user's project). Give it a unique name with a timestamp, e.g. `questions-20261015-1632.json`, so parallel Claude sessions never overwrite each other's files.
2. Run with the **Bash** tool and `run_in_background: true` (the user may take minutes; you are notified automatically when they submit):

   ```
   sh "<this skill's base directory>/scripts/ask-form.sh" "<path to questions.json>"
   ```

   Run it from the project directory: the header shows the current directory's name as a project label (so the user can tell forms from parallel sessions apart); override with `--project NAME`. A short system sound plays when the form opens (add `--quiet` to skip it). Then tell the user in one line that the form is open in their browser (include the URL from the first output line in case it did not open), and stop — do not poll.
3. When the background task finishes, read its output: a readable summary of every answer. The full JSON is in `<questions>.answers.json` next to the questions file. Exit code 2 = timed out (default 2 h) or interrupted, no answers.
4. Act on the answers. "The user leaves this decision to you" means pick your recommended option yourself and say what you picked. Read the notes — they often carry the important nuance.

## questions.json

```json
{
  "title": "New online shop — details",
  "intro": "Optional text above the questions. Supports `code`, **bold** and newlines.",
  "questions": [
    {
      "id": "db",
      "section": "Backend",
      "question": "Which database should we use?",
      "context": "Why you ask / what it affects. Optional, keep it to 1–2 sentences.",
      "type": "single",
      "options": [
        { "label": "PostgreSQL", "description": "Robust, already in your infrastructure.", "recommended": true },
        { "label": "SQLite", "description": "Simplest, a single file." }
      ]
    },
    {
      "id": "payments",
      "question": "Which payment methods should we support?",
      "type": "multi",
      "options": [
        { "label": "Card", "recommended": true },
        { "label": "Bank transfer", "recommended": true },
        { "label": "Apple Pay / Google Pay" }
      ]
    },
    {
      "id": "gateway",
      "question": "Which card payment gateway?",
      "showIf": { "question": "payments", "anyOf": ["Card"] },
      "options": [
        { "label": "Stripe", "recommended": true, "preview": "stripe.paymentIntents.create({\n  amount: 1990, currency: 'eur'\n})" },
        { "label": "Adyen" }
      ]
    },
    {
      "id": "priorities",
      "question": "Rank what matters most.",
      "type": "rank",
      "options": [{ "label": "Page speed" }, { "label": "Running costs" }, { "label": "Easy administration" }]
    },
    {
      "id": "seo",
      "question": "How important is SEO?",
      "type": "scale", "min": 1, "max": 5, "minLabel": "not at all", "maxLabel": "critical"
    },
    {
      "id": "brand",
      "question": "Describe in a few words how the shop should feel.",
      "type": "text",
      "placeholder": "e.g. minimalist, premium…"
    },
    {
      "id": "more",
      "question": "Anything else I should know?",
      "type": "text",
      "optional": true
    }
  ]
}
```

- `type`:
  - `single` (radio), `multi` (checkboxes) — need `options`.
  - `text` — free text; optional `default`, `placeholder`, `rows`.
  - `rank` — the user orders `options` by drag & drop or arrow buttons; the order you give is the starting (pre-filled) order, so list them in your recommended order. Optional `hint` replaces the default instruction text. No "Other".
  - `scale` — integer scale, `min`/`max` (default 1–5, at most 11 steps), optional `minLabel`/`maxLabel` for the ends, optional `default` to pre-select.
  - Defaults to `single` when `options` exist, else `text`.
- `preview` on an option shows a monospace block (code snippet, ASCII mockup, file tree) under it — use it when the user compares concrete variants. Use `\n` for new lines.
- `showIf: {"question": "<id>", "anyOf": [...]}` shows the question only when the referenced question's answer includes one of the values (option labels; `"__other"` for "Other"; numbers for a scale). Hidden questions are not required. Use it instead of asking follow-ups that make sense only for one answer — this keeps big forms short.
- `recommended: true` pre-selects the option and shows a "recommended" badge — mark your recommendation whenever you have one; the user often just confirms.
- An "Other" free-text choice is added to `single`/`multi` automatically; set `"allowOther": false` to remove it.
- `optional: true` lets the user leave the question empty (shown with an "optional" badge, not counted in progress). Always use it for catch-all questions like "Anything else?"; everything else stays required.
- `section` groups consecutive questions under a heading — use it when there are ~8+ questions.
- The UI chrome (buttons, hints, badges) is English. Write `title`, `intro`, questions and options in the user's language.
- `id` is optional (defaults to q1, q2, …) but stable, meaningful ids make the answers easier to use.

Every visible non-optional question must be answered before submitting (the user may instead click "Let Claude decide" on a question). Each question also takes an optional note.

## Answer format

```json
{ "id": "payments", "question": "...", "type": "multi",
  "selected": ["Card"], "other": "PayPal", "delegated": false, "note": "" }
```

`other` is `null` when not chosen. By type, instead of `selected`/`other`: `text` → `"text"`; `rank` → `"ranking": [labels, first = top]`; `scale` → `"value": 4` plus `"scale": {min, max, minLabel, maxLabel}`. A question hidden by `showIf` has `"hidden": true` — ignore it (it did not apply). Optional questions also carry `"optional": true` and `"skipped"` (true when left empty — treat it as "nothing to add").

## WSL

Works the same: the server binds to 127.0.0.1 inside WSL, which WSL2 forwards to Windows' localhost, and the page opens in the Windows browser (via `wslview` or `cmd.exe /c start`). If the browser does not open, give the user the printed URL.
