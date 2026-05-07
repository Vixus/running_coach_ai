# Claude Prompt Contracts

Three distinct Claude calls happen in this feature. Each has a stable response shape that the system parses; deviations are caught and either retried (once) or aborted with a logged warning.

All calls use the existing `running_coach_ai.coach.persona.call_claude(system_prompt, messages, max_tokens)` wrapper — no new SDK pattern.

---

## C-001: Adaptive question generation (per turn within a session)

**When**: every time the system needs to issue the next question in an open `StoryInterviewSession`.

**Trigger code path**: `coach.story.next_question(session, db)` → `call_claude(...)`.

**System prompt skeleton**:

```text
You are a veteran magazine interviewer building a positive narrative about an athlete's running. You are part-way through an interview triggered by: <trigger_kind label>.

Athlete profile:
- Name: <first name>
- Age: <age>
- Goal: <race or block summary>
- Recent training context: <2–3 line summary>

Session-so-far transcript:
Q1: <text> → A1: <answer>
Q2: <text> → A2: <answer>
... (up to 9 prior Q&As)

Your job: ask ONE more question that builds on what they just told you. Make it specific, warm, and curious — not generic. Then output a JSON object with:
  {"question": "<the question>",
   "options": ["<option 1>", "<option 2>", "<option 3>", "<option 4>"],  // 2 to 4 options
   "session_complete": false}

Set session_complete=true only when you've collected enough material for a 400–600 word editorial (typically 5–8 questions) AND further questions would feel padding rather than illuminating.

Output ONLY the JSON. No prose around it.
```

**User message**: `Ask the next question.`

**Expected response**: pure JSON parseable as:

```json
{
  "question": "What was going through your head at mile 20?",
  "options": [
    "I was just trying to survive",
    "I felt strong and present",
    "I was thinking about the finish",
    "Something else"
  ],
  "session_complete": false
}
```

**Validation**:
- Top-level object with exactly the three keys.
- `question`: non-empty string ≤ 500 chars.
- `options`: list of 2–4 non-empty strings, each ≤ 100 chars.
- `session_complete`: boolean.

**On parse failure**: retry once with the same prompt + `"Your previous output failed to parse as JSON. Try again."` appended. If still malformed, abort the session: set `completed_at = now()`, leave `skipped = False`, write a `story_failed` notification to the inbox, log a warning.

**Token budget**: `max_tokens=512` — plenty for one question + four short options.

**Latency target**: ≤ 2 s (one Claude call, small prompt).

---

## C-002: Editorial generation + template vote (one call per generation)

**When**: a milestone fires AND the athlete has answered ≥4 questions across all linked sessions OR an admin triggers manual generation.

**Trigger code path**: `coach.story.generate_story(athlete, milestone_type, db)` → `call_claude(...)`.

**System prompt skeleton (template_locked_by_athlete = False)**:

```text
You are a magazine editor writing a 400–600 word editorial profile of an athlete after a key training milestone. Below is the athlete's profile, training stats, and the answers they gave in interview sessions tied to this milestone.

Athlete profile:
- Name: <full name>
- Age: <age>
- Goal: <goal summary, target time, race date>

Training stats for this milestone period:
- Total miles: <N>
- Weeks of training: <N>
- Race time (if applicable): <H:MM:SS>
- Avg HRV trend: <trend>
- Notable workouts: <2–3 line summary>

Interview answers (Q&A pairs from <N> sessions):
Q1: <question> → A1: <answer>
... (all linked Q&As, deduplicated)

Available magazine templates (pick exactly one):
- vogue (Editorial Profile): glossy, aspirational, present-tense scene-setting; opens with a vignette
- runners_world (The Build-Up): practical reportage, second person, training-stat sidebars
- times_long_read (The Long Read): third-person observational, like a Sunday magazine profile
- outside (The Field Notes): adventure prose, sensory, weather-aware, first person
- gq_profile (The Profile): polished, journalistic, achievement-focused; pull quotes mid-spread

Trigger-affinity hints (optional prior, not binding):
- race_complete + ultra distance → outside
- race_complete + city marathon/half → vogue or gq_profile
- pace_recalibration session contributing → times_long_read or runners_world
- pr_set → runners_world or gq_profile
- pace_recalibration → times_long_read

Choose the template whose voice best fits THIS athlete's answers and arc — don't just match the trigger.

Write the editorial in the chosen template's voice. Output ONLY a JSON object:
{
  "editorial_body": "<400–600 word prose, 3–5 paragraphs>",
  "template_key": "<one of: vogue, runners_world, times_long_read, outside, gq_profile>"
}
```

**System prompt skeleton (template_locked_by_athlete = True)**:

Same as above, but the "Available magazine templates" + "Trigger-affinity hints" + "Choose the template" sections are replaced with:

```text
Write in this voice:
<voice_description from the locked template's template.json>

Output ONLY a JSON object:
{
  "editorial_body": "<400–600 word prose, 3–5 paragraphs>"
}
```

**User message**: `Write the editorial.`

**Validation**:
- Top-level object with exactly the expected keys.
- `editorial_body`: string with word count between 350 and 700 (a tolerance band around 400–600).
- `template_key`: string, must be a key in the registry. (Locked-template path skips this check.)

**On parse failure**:
- Retry once with `"Your previous output failed to parse as JSON. Try again."` appended.
- If still malformed: write a `story_failed` notification, log error, mark the story creation/regeneration as failed, return without overwriting the existing `editorial_body` (if regenerating) or leave the row in an unpublishable state (if creating).

**On template_key out of registry**: fall back to highest-affinity template for the trigger; if none, fall back to `vogue`. Log a warning.

**Token budget**: `max_tokens=4096` — comfortably more than 600 words at ~1.5 tokens/word + JSON overhead.

**Latency target**: ≤ 30 s (per SC-S001). Empirically, Claude Sonnet 4.6 produces 600 words in ~10–20 s.

---

## C-003: Trigger-event narrative summarizer (helper, optional)

**When**: building the system prompt for C-001 or C-002, the system needs a one-line natural-language label for the trigger event ("the Brooklyn Half on May 4", "your fastest 10K so far", "a tough 12-mile long run last Saturday"). This call is OPTIONAL — for v1 we generate these labels in Python from the trigger context, not via Claude. Documented here so the contract is explicit about which prompts ARE and ARE NOT Claude-driven.

**Decision**: NOT a Claude call in v1. Implemented as `coach.story.format_trigger_label(trigger_kind, context, athlete, db)` returning a string. Reduces Claude API cost and latency. Move to a Claude call in v1.1 only if the Python labels feel robotic.

---

## Prompt-versioning convention

Each prompt template is versioned in code via a constant at the top of `coach/story.py`:

```python
QUESTION_PROMPT_VERSION = "v1"
EDITORIAL_PROMPT_VERSION = "v1"
```

When a prompt changes substantively, bump the version and add a comment explaining the change. This lets retrospective analysis (and the A/B testing we may do later) attribute generation quality to specific prompt versions.

`StoryInterviewSession.trigger_context_json` and `AthleteStory.editorial_body` do NOT explicitly record the prompt version they were generated under — the version is recoverable from `created_at` + git history, which is sufficient for v1.
