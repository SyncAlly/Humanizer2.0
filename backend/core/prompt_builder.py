"""
core/prompt_builder.py

Constructs the system prompt sent to Gemini for each humanize request.

This is the intellectual core of the project. The prompt is not generic —
it is dynamically assembled from three sources:

1. The user's measured style profile (burstiness, contraction rate, etc.)
2. The requested rewrite strength (light / medium / aggressive)
3. A hard-coded anti-detection ruleset targeting the specific signals
   that GPTZero, Turnitin, and similar tools measure

The result is a system prompt that tells the model exactly how to write
to match this specific person, while simultaneously avoiding every
statistical fingerprint that detectors look for.

Research basis for the anti-detection rules:
- GPTZero:  measures per-token perplexity and burstiness (sentence length variance)
- Turnitin: trained on student writing; flags grammar perfection, hedge patterns,
            and structural predictability
- Universal: high-probability token choices, AI filler vocabulary, even rhythm,
             absence of personal voice
"""

from models.schemas import StyleProfile, HumanizeStrength


# ── AI filler words — banned in all outputs ────────────────────────────────
# These appear at statistically anomalous rates in AI-generated text and are
# now primary detection signals. Never allow them in output.
AI_FILLER_WORDS: list[str] = [
    "furthermore", "additionally", "notably", "crucial", "delve",
    "it is important to", "it is worth noting", "moreover", "thus",
    "hence", "nevertheless", "notwithstanding", "importantly",
    "significantly", "in conclusion", "to summarize", "in summary",
    "one must", "it should be noted", "undoubtedly", "certainly",
    "it is evident", "as previously mentioned", "in today's world",
    "in the realm of", "landscape", "game-changer", "paradigm",
    "utilize", "leverage", "robust", "seamless", "groundbreaking",
    "revolutionize", "streamline", "cutting-edge", "synergy",
]

# ── Strength instruction blocks ────────────────────────────────────────────
STRENGTH_INSTRUCTIONS: dict[str, str] = {
    HumanizeStrength.light: """
REWRITE STRENGTH: Light
- Preserve 80%+ of the original phrasing and structure.
- Focus only on: removing AI filler words, varying sentence rhythm slightly,
  and replacing 1-2 overly formal word choices per paragraph.
- Do not restructure sentences or change the writing voice dramatically.
- Minimal intervention — the goal is to sand down the obvious AI tells only.
""".strip(),

    HumanizeStrength.medium: """
REWRITE STRENGTH: Medium
- Rephrase at least 40% of sentences while keeping all meaning intact.
- Visibly mix sentence lengths — short punchy sentences alongside longer ones.
- Add at least one natural personal voice marker per paragraph.
- Replace predictable word choices with less expected but equally correct ones.
- Remove all structural predictability (no topic sentence every paragraph).
""".strip(),

    HumanizeStrength.aggressive: """
REWRITE STRENGTH: Aggressive
- Substantially rewrite the text from the ground up.
- Change sentence order within paragraphs where meaning permits.
- Break long compound sentences into fragments or run-ons as a human would.
- Combine short adjacent sentences into a single longer flowing one occasionally.
- The output should feel structurally unrecognizable from the input
  while preserving every fact and idea exactly.
""".strip(),
}


def _build_profile_section(profile: StyleProfile) -> str:
    """Translates a StyleProfile into concrete writing instructions."""

    contraction_instruction = (
        "Use contractions freely — don't, can't, I'm, won't, it's, they're."
        if profile.contraction_rate > 0.04
        else "Avoid contractions; this person writes in a more formal register."
    )

    first_person_instruction = (
        f"Use first-person naturally (I, me, my) — this person's rate is "
        f"{profile.first_person_rate:.1%} of words."
        if profile.first_person_rate > 0.015
        else "Minimize first-person; this person writes in an impersonal or third-person style."
    )

    burstiness_instruction = (
        f"This person has high sentence length variation (burstiness: {profile.burstiness:.1f}). "
        f"Match this — alternate between very short sentences (3-6 words) and long ones (20-30 words) "
        f"unpredictably. Never write three sentences of similar length in a row."
        if profile.burstiness > 7
        else
        f"Burstiness score is {profile.burstiness:.1f} — introduce more variation than the original. "
        f"Target a mix of sentence lengths even if the samples were uniform."
    )

    fragment_instruction = (
        "This person uses sentence fragments naturally. Like this. Short ones. Use them."
        if profile.fragment_rate > 0.05
        else "Keep sentences grammatically complete — this person does not use fragments."
    )

    dash_instruction = (
        "Use em dashes for interruptions, asides, or pivots — like a spoken thought breaking off."
        if profile.uses_dash
        else "Avoid em dashes."
    )

    ellipsis_instruction = (
        "Occasional ellipsis is fine for trailing thoughts..."
        if profile.uses_ellipsis
        else "Avoid ellipsis."
    )

    exclamation_instruction = (
        "Exclamation marks are acceptable occasionally."
        if profile.exclamation_rate > 0.1
        else "Avoid exclamation marks — this person writes with low emotional punctuation."
    )

    vocab_section = ""
    if profile.distinctive_words:
        top_words = ", ".join(profile.distinctive_words[:12])
        vocab_section = (
            f"\nCharacteristic vocabulary this person uses: {top_words}. "
            f"Incorporate these naturally where they fit."
        )

    hedge_section = ""
    if profile.found_hedges:
        hedge_section = (
            f"\nNatural hedge phrases this person uses: {', '.join(profile.found_hedges)}. "
            f"Include at least one per response."
        )

    return f"""
== USER STYLE PROFILE ({profile.sample_count} samples, {profile.word_count:,} words) ==

Sentence rhythm:
- Average sentence length: {profile.avg_sentence_length:.0f} words. Mirror this closely.
- {burstiness_instruction}
- {fragment_instruction}

Word choices:
- {contraction_instruction}
- {first_person_instruction}
- Average word length: {profile.avg_word_length:.1f} chars. {"Prefer shorter, direct words." if profile.avg_word_length < 5 else "Latinate vocabulary is acceptable — this person uses longer words."}
- {exclamation_instruction}

Punctuation:
- {dash_instruction}
- {ellipsis_instruction}
- {"Semicolons are acceptable." if profile.uses_semicolon else "Avoid semicolons."}
{vocab_section}
{hedge_section}
""".strip()


def _build_no_profile_section() -> str:
    """Fallback instructions when no profile exists."""
    return """
== NO STYLE PROFILE — GENERAL HUMANIZATION ==
- Mix sentence lengths unpredictably: some 3-5 word sentences, some 20-30 word ones.
- Write as a thoughtful, informal person speaking to a peer — not a document.
- Include at least one first-person observation or opinion if the content allows.
- Use contractions naturally.
- Include one sentence that feels like a spoken mid-thought or pivot.
""".strip()


def _build_anti_detection_section() -> str:
    """The universal anti-detection ruleset — applied regardless of profile."""
    banned = ", ".join(AI_FILLER_WORDS[:20])  # first 20 in the prompt, all blocked in spirit
    return f"""
== ANTI-DETECTION RULES (apply to every output, no exceptions) ==

Banned words and phrases — never use any of these:
{banned}, [and all similar AI-characteristic filler phrases]

Perplexity injection:
- Replace at least 20% of common adjectives and verbs with less statistically
  expected but equally correct alternatives.
- When two word choices are equivalent, always pick the less obvious one.
- Avoid the most probable next word whenever a slightly surprising one fits.

Rhythm rules:
- Never write three consecutive sentences of similar length.
- At least one sentence per paragraph should feel like a genuine spoken
  thought — slightly informal, a mid-idea pivot, or an incomplete structure.
- Do not start every paragraph with a topic sentence.
- Transition between ideas without transition words — just cut to it.

Grammar and formality:
- Do not correct every grammar edge case — slight informality is human.
- Vary punctuation: mix commas, dashes, and periods as clause separators.
- One-sentence paragraphs for emphasis are fine.

Structure:
- Do not follow a strict intro-body-conclusion arc unless the original does.
- Do not mirror the original's paragraph structure if strength is medium or aggressive.
""".strip()


def build_system_prompt(
    profile: StyleProfile | None,
    strength: HumanizeStrength,
) -> str:
    """
    Main entry point — assembles the complete system prompt.

    Args:
        profile:  The user's StyleProfile, or None for generic humanization.
        strength: How aggressively to rewrite.

    Returns:
        A complete system prompt string ready to pass to the Gemini API.
    """
    strength_block     = STRENGTH_INSTRUCTIONS[strength]
    profile_block      = _build_profile_section(profile) if profile else _build_no_profile_section()
    anti_detect_block  = _build_anti_detection_section()

    return f"""You are a writing assistant that rewrites text to sound naturally human.
Your output must pass AI detection tools including GPTZero and Turnitin.
Your output must also sound like the specific person described in the style profile below.

{strength_block}

{profile_block}

{anti_detect_block}

== OUTPUT RULES ==
- Output ONLY the rewritten text. No preamble, no labels, no commentary.
- Do not say "Here is the rewritten text:" or anything similar.
- Preserve every fact, idea, technical term, and piece of data from the input exactly.
- Do not add information that was not in the original text.
- Do not remove any ideas from the original text.
- Match the approximate length of the original (within 20%).
""".strip()


def build_user_message(text: str) -> str:
    """
    Wraps the input text in a consistent user message format.
    Keeping this separate from the system prompt makes it easy to
    add pre-processing steps (e.g. chunking long texts) in one place.
    """
    return f"Rewrite the following text:\n\n{text}"
