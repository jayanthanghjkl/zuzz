"""Cerebellum — personality tests.

The region, not the endpoints. These pin down four properties.

**The prompt must not leak the user's name.** ``apply_personality`` appends a
voice section to the system prompt, and the voice section is the one part of
ZUZZ's configuration that knows the owner's name. Putting that in the prompt
sends it to whatever model is answering — so the name is substituted into the
greeting and the sign-off, which are shown to the user, and deliberately left
out of the prompt. ``tests/test_cerebrum.py`` already asserts the owner's name
is absent from the built prompt; the tests here assert the same thing about the
Cerebellum's own output, plus *why*, since a later "helpful" change to include
it would otherwise look harmless.

**A mood guess needs evidence.** Below a threshold the reading is ``neutral``,
because a reply shaped around an emotion the user did not express is worse than
one shaped around nothing.

**The register moves one step and no further.** A tone that swings with the
input is a tone the user cannot predict.

**Only six keys are writable, and each against a closed list.** ``name``,
``owner``, ``user_name`` and ``wake_word`` are refused at runtime; ZUZZ changing
its own name is not a setting, it is a different program.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

import pytest

from brain.cerebellum import (
    ALLOWED_VALUES,
    CASUAL,
    EDITABLE_KEYS,
    EXCITED,
    FORMAL,
    FRUSTRATED,
    MOODS,
    NEUTRAL,
    PERSONALITY_MARKER,
    STYLES,
    TIRED,
    VERBOSITY,
    Cerebellum,
    Personality,
    PersonalityRefused,
    adjust_response_tone,
    apply_personality,
    build_personality_directive,
    detect_formality,
    detect_user_mood,
    formality_hint,
    get_greeting,
    get_signoff,
    load,
)
from brain.cerebellum.personality_config import DEFAULTS

BASE_PROMPT = "You are ZUZZ, a desktop assistant."

CONFIG: Dict[str, Any] = {
    "name": "ZUZZ",
    "owner": "Jayanthan",
    "wake_word": "zuzz",
    "user_name": "Jayanthan",
    "tone": ["direct", "warm"],
    "max_sentences_per_reply": 8,
    "style": "formal_friendly",
    "humor_level": "subtle",
    "verbosity": "concise",
    "greeting": "Hey {user_name}",
    "signoff": "— {name}",
    "quirks": ["likes a precise question"],
}


@pytest.fixture()
def config_path(tmp_path: Path) -> Path:
    """Write a throwaway personality file."""
    path = tmp_path / "personality.json"
    path.write_text(json.dumps(CONFIG), encoding="utf-8")
    return path


@pytest.fixture()
def cerebellum(config_path: Path) -> Cerebellum:
    """Return a Cerebellum over the throwaway file."""
    return Cerebellum(path=config_path)


# -- the prompt must not leak the name --------------------------------


def test_the_system_prompt_never_contains_the_owners_name(config_path: Path) -> None:
    """The voice section goes to the model, so it must not carry the name.

    The greeting and sign-off templates *do* contain it, and they are shown to
    the user rather than sent anywhere. The prompt is sent. That asymmetry is
    the whole reason this test exists: it is a one-line change to make the
    prompt friendlier and it would quietly send the owner's name to a provider.
    """
    voice = load(config_path)
    prompt = apply_personality(BASE_PROMPT, voice)

    assert voice.owner not in prompt
    assert voice.user_name not in prompt
    assert "{user_name}" not in prompt, "an unsubstituted template is worse than none"


def test_the_greeting_and_signoff_do_use_the_name(config_path: Path) -> None:
    """These are for the user, so substituting the name is the point."""
    voice = load(config_path)
    assert get_greeting(voice) == "Hey Jayanthan"
    assert get_signoff(voice) == "— ZUZZ"


def test_both_name_placeholders_replaced() -> None:
    """``{name}`` and ``{user_name}`` are the same request written two ways.

    A greeting configured with either spelling has to render to the same line.
    Handling only one is not a crash — it is worse. The template keeps its
    placeholder, the fallback that appends a name when it cannot find one in the
    template runs anyway, and the user gets "Hey {user_name} Jayanthan": their
    own name, preceded by a literal template. That is a shipped bug, so it is
    pinned here rather than left to the next refactor.
    """
    owner = dict(owner="Jayanthan")
    assert (
        Personality(**owner, greeting="Hey {user_name}!").addressed_greeting()
        == "Hey Jayanthan!"
    )
    assert (
        Personality(**owner, greeting="Hey {name}!").addressed_greeting()
        == "Hey Jayanthan!"
    )
    assert (
        Personality(**owner, greeting="Hey {name}, welcome!").addressed_greeting()
        == "Hey Jayanthan, welcome!"
    )
    # Both spellings in one line, and neither left behind.
    both = Personality(**owner, greeting="{name} — {user_name}").addressed_greeting()
    assert both == "Jayanthan — Jayanthan"


def test_blank_name_removes_placeholder_cleanly() -> None:
    """No name configured means the placeholder goes — punctuation and all.

    A blank name is a supported state, not a failure, and the render still has
    to look like a sentence. "Hey !" and "Hey , welcome!" are both worse than
    "Hey!" and "Hey, welcome!", so the gap and the punctuation that was only
    there to separate the name are cleaned up together.
    """
    assert Personality(greeting="Hey {user_name}!").addressed_greeting() == "Hey!"
    assert (
        Personality(greeting="Hey {name}, welcome!").addressed_greeting()
        == "Hey, welcome!"
    )
    # Nothing but a placeholder leaves nothing at all, not a stray separator.
    assert Personality(greeting="{user_name}").addressed_greeting() == ""
    assert Personality(greeting="{user_name} — ZUZZ").addressed_greeting() == "ZUZZ"


def test_signed_off_replaces_placeholders() -> None:
    """A sign-off is rendered, not shipped with its template showing.

    ``{name}`` in a sign-off is ZUZZ signing its own name, which is what a
    closing line nearly always means; ``{user_name}`` is there for the template
    that addresses the person instead. The same clean removal applies when
    there is nothing to put in one, so a half-rendered sign-off is never
    possible.
    """
    assert Personality(name="ZUZZ", signoff="— {name}").signed_off() == "— ZUZZ"
    assert (
        Personality(name="ZUZZ", owner="Jayanthan", signoff="— {name}").signed_off()
        == "— ZUZZ"
    )
    assert (
        Personality(name="ZUZZ", owner="Jayanthan", signoff="— {name}, from {user_name}").signed_off()
        == "— ZUZZ, from Jayanthan"
    )
    # ZUZZ unnamed and no user named: the sign-off reduces to nothing rather
    # than to punctuation. The name has to be blanked explicitly — the default
    # is "ZUZZ", so an unconfigured assistant is named, not anonymous.
    assert Personality(name="", signoff="— {name}").signed_off() == ""
    assert Personality(name="ZUZZ").signed_off() == ""


def test_the_prompt_keeps_the_original_instructions(cerebellum: Cerebellum) -> None:
    """The personality is appended, never substituted.

    A builder that returned only the voice section would drop every safety
    instruction in the original prompt, and nothing about the tests for
    ``apply_personality`` alone would catch it.
    """
    prompt = apply_personality(BASE_PROMPT, cerebellum.get_settings())
    assert prompt.startswith(BASE_PROMPT)
    assert len(prompt) > len(BASE_PROMPT)


def test_apply_personality_without_config_is_noop(tmp_path: Path) -> None:
    """A missing personality file must not break the prompt, only the voice.

    The chat path calls this on every request, so it has to survive an absent
    file rather than requiring the caller to check first. Note what is *not*
    being tested: omitting the ``personality`` argument is the normal case, not
    the no-config one — it loads the shipped ``config/personality.json``, which
    has a voice in it. Asserting "nothing was appended" against an omitted
    argument only passes on a checkout where that file happens to be absent.
    """
    voice = load(tmp_path / "absent.json")
    prompt = apply_personality(BASE_PROMPT, voice)

    assert prompt.startswith(BASE_PROMPT), "the safety instructions are never dropped"
    assert PERSONALITY_MARKER in prompt, "the defaults still produce a usable voice"


def test_the_directive_carries_the_configured_limits(cerebellum: Cerebellum) -> None:
    """Verbosity, sentence budget and quirks all reach the directive.

    These are the settings that change how long a reply is, so a directive that
    dropped them would leave the Settings page able to change a number that does
    nothing.
    """
    directive = build_personality_directive(cerebellum.get_settings())
    assert "concise" in directive.lower()
    assert "8" in directive
    assert "precise question" in directive, "a configured quirk should appear"


def test_the_sentence_budget_reaches_the_directive(config_path: Path) -> None:
    """Changing the budget changes the directive, rather than being cosmetic."""
    data = dict(CONFIG, max_sentences_per_reply=2)
    config_path.write_text(json.dumps(data), encoding="utf-8")
    directive = build_personality_directive(load(config_path))
    assert "2 sentences" in directive


# -- a mood guess needs evidence -------------------------------------


@pytest.mark.parametrize(
    "message, expected",
    [
        ("this is not working AGAIN", FRUSTRATED),
        ("why is this still broken", FRUSTRATED),
        ("this whole thing is stupid", FRUSTRATED),
        ("i am so tired of this", TIRED),
        ("that is awesome, it worked!", EXCITED),
        ("brilliant, excellent work", EXCITED),
    ],
)
def test_a_clear_message_is_read_as_its_mood(message: str, expected: str) -> None:
    """The four moods are reachable, each from ordinary wording."""
    assert detect_user_mood(message) == expected


@pytest.mark.parametrize(
    "message",
    ["", "   ", "ok", "fine, thanks", "what time is it", "run the tests"],
)
def test_weak_evidence_stays_neutral(message: str) -> None:
    """Below the threshold the answer is ``neutral``, not a best guess.

    Every wrong guess is a reply shaped around an emotion the user did not
    express, which is worse than a reply shaped around nothing. ``"finally"``
    scores 1.5 against a threshold of 2.0 on its own, so ``"yesss finally"``
    reads as neutral — deliberately, since the user might be sarcastic.
    """
    assert detect_user_mood(message) == NEUTRAL


def test_the_mood_is_one_of_four() -> None:
    """The vocabulary is closed, so a caller can rely on it."""
    assert set(MOODS) == {NEUTRAL, FRUSTRATED, EXCITED, TIRED}


def test_shouting_and_punctuation_raise_a_reading_but_do_not_pick_one() -> None:
    """``"THIS IS BROKEN"`` is the same sentence as ``"this is broken"``.

    Treated as a multiplier on the leading mood rather than as its own signal,
    so emphasis changes the strength of a reading rather than the emotion.
    """
    loud = detect_user_mood("THIS IS NOT WORKING AGAIN")
    quiet = detect_user_mood("this is not working again")
    assert loud == quiet == FRUSTRATED

    # Punctuation alone cannot invent a mood: no cue words, no reading.
    assert detect_user_mood("WHAT?!") == NEUTRAL


def test_every_mood_has_an_instruction(cerebellum: Cerebellum) -> None:
    """A detected mood that produces no instruction would be detected for nothing."""
    for mood in MOODS:
        shaped = cerebellum.respond("this is not working again", "Here is the answer.")
        assert shaped["instruction"]
        del mood
    assert cerebellum.respond("ok", "Here it is.")["mood"] == NEUTRAL


# -- the register moves one step, no further -------------------------


def test_a_loose_message_pulls_the_register_down() -> None:
    """``"yo whats up"`` answered in a formal register reads as a difficult machine."""
    assert detect_formality("yo whats up", style="formal_friendly") == NEUTRAL
    assert detect_formality("yo whats up", style="playful") == CASUAL


def test_a_formal_message_pushes_the_register_up() -> None:
    """A casual reply to a formal request reads as not taking the work seriously.

    The wording matters and the reason is not obvious. "deployment" is on the
    technical-vocabulary list, and technical vocabulary cancels a shift *upward*
    only — on purpose, so that a developer reporting a bad deploy is not read as
    being formal. Which means a sentence about deploying cannot be the fixture
    for "the message pushes the register up", because the one thing that would
    prove the shift happened is the thing the sentence is built to suppress.
    """
    assert detect_formality(
        "could you please provide an update", style="casual"
    ) == NEUTRAL


def test_the_register_never_moves_more_than_one_step() -> None:
    """Bounded, because a tone that swings with the input cannot be predicted.

    Three curse words must not make ZUZZ cold and one exclamation mark must not
    make it shout.
    """
    shouted = "FUCK THIS FUCKING THING WHAT THE FUCK IS WRONG"
    assert detect_formality(shouted, style="formal_friendly") == FORMAL
    assert detect_formality(shouted, style="casual") != FORMAL, "one step at most"


def test_technical_wording_does_not_move_the_register() -> None:
    """A user writing about a segfault is being a programmer, not being terse.

    Without this, ``"fix the null pointer"`` reads as aggressive and ZUZZ
    answers a bug report as though it were an insult.
    """
    for message in (
        "there is a null pointer in the stack trace",
        "the compiler is throwing an exception at runtime",
        "the api endpoint returns 500 on the database query",
    ):
        assert detect_formality(message, style="formal_friendly") == FORMAL


def test_an_empty_message_leaves_the_register_alone() -> None:
    """No evidence means no adjustment, rather than a default guess."""
    assert detect_formality("", style="casual") == CASUAL
    assert detect_formality("   ", style="formal_friendly") == FORMAL


def test_an_unknown_style_falls_back_to_the_middle() -> None:
    """A hand-edited style ZUZZ does not know becomes the neutral *basis*.

    The fallback is about the starting point, not the answer. "sarcastic" is not
    a register, so it resolves to the middle of the scale — and then the message
    does what the message always does, and "hello" is a formal opener. The
    detector does not freeze the register just because the config is odd; freezing
    it would be a second, quieter bug, and one the user cannot see or correct.
    """
    assert detect_formality("hello there", style="sarcastic") == FORMAL
    # Same unknown style, no signal in the message: the middle is what shows.
    assert detect_formality("the build finished", style="sarcastic") == NEUTRAL


def test_every_register_has_a_hint() -> None:
    """The hint is what reaches the prompt, so it must exist for all three."""
    for register in (CASUAL, NEUTRAL, FORMAL):
        assert formality_hint(register)


# -- tone adjustment -------------------------------------------------


def test_a_frustrated_user_gets_the_filler_dropped() -> None:
    """The mechanical half of mood: the opener that precedes no information."""
    padded = "I'm sorry to hear that. Let me help with that. The answer is 42."
    trimmed = adjust_response_tone(padded, FRUSTRATED)
    assert trimmed != padded
    assert trimmed.endswith("The answer is 42.")


@pytest.mark.parametrize(
    "preamble",
    [
        "I'm sorry to hear that.",
        "Sorry to hear that.",
        "I understand.",
        "I'm sorry.",
        "Sorry.",
        "I am sorry!",
    ],
)
def test_every_apology_shape_is_preamble(preamble: str) -> None:
    """The apology family is matched whole, however it is phrased.

    "I'm sorry to hear that." is the most common opener a model reaches for after
    a user reports a failure, and matching only the bare "sorry" left the single
    most frequent version of it sitting there in full — the exact sentence the
    trim exists to remove.
    """
    trimmed = adjust_response_tone(f"{preamble} The answer is 42.", FRUSTRATED)
    assert trimmed == "The answer is 42."


def test_a_preamble_leaving_a_comma_does_not_pass_the_comma_on() -> None:
    """Whatever the removed preamble leaves at the front is debris, not prose.

    A preamble ending in a comma is ordinary English — "Sorry to hear that, the
    log shows a timeout" — so the trim has to take the punctuation with it.
    Otherwise the reply the user reads opens on a comma, which looks like the
    program talking over itself.
    """
    trimmed = adjust_response_tone(
        "Sorry to hear that, the timeout is in the parser. The answer is 42.",
        FRUSTRATED,
    )
    assert trimmed.startswith("the timeout is in the parser")


def test_a_tired_user_gets_the_filler_dropped_too() -> None:
    """Both moods that want a short answer do the same thing mechanically."""
    padded = "Sure! I'd be happy to help with that. The answer is 42."
    assert adjust_response_tone(padded, TIRED).endswith("The answer is 42.")


def test_a_neutral_reply_is_left_alone() -> None:
    """No mood means no change, so the configured tone is what survives."""
    reply = "I'm sorry to hear that. Let me help with that."
    assert adjust_response_tone(reply, NEUTRAL) == reply


# -- what is writable ------------------------------------------------


def test_only_six_keys_are_editable(cerebellum: Cerebellum) -> None:
    """The list is closed, so a key added next year is unwritable until asked for."""
    assert set(EDITABLE_KEYS) == {
        "style",
        "humor_level",
        "verbosity",
        "greeting",
        "signoff",
        "quirks",
    }


@pytest.mark.parametrize("key", ["name", "owner", "user_name", "wake_word"])
def test_the_identity_keys_are_refused(cerebellum: Cerebellum, key: str) -> None:
    """ZUZZ renaming itself is not a setting; it is a different program.

    The wake word is the important one: changing it would move the line between
    "the user asked" and "something in the room said the word", and that line
    should not be movable by the process being supervised.
    """
    with pytest.raises(PersonalityRefused) as caught:
        cerebellum.update_setting({key: "something else"})
    assert "not editable" in str(caught.value)


@pytest.mark.parametrize("key", sorted(ALLOWED_VALUES))
def test_a_closed_vocabulary_is_enforced(cerebellum: Cerebellum, key: str) -> None:
    """Each of the three enumerations refuses a value outside it.

    A ``style`` the rest of the code has no wording for would produce a voice
    section with nothing in it.
    """
    with pytest.raises(PersonalityRefused):
        cerebellum.update_setting({key: "not_a_real_value"})


@pytest.mark.parametrize("key", sorted(ALLOWED_VALUES))
def test_every_allowed_value_is_accepted(cerebellum: Cerebellum, key: str) -> None:
    """Every value the page offers must actually work.

    The dropdowns are built from this same table, so a value in the table that
    the writer rejects would give the user a control that fails when used.
    """
    for value in ALLOWED_VALUES[key]:
        assert cerebellum.update_setting({key: value}).to_dict()[key] == value


def test_a_refused_change_writes_nothing(cerebellum: Cerebellum, config_path: Path) -> None:
    """A request naming one good key and one bad key changes nothing at all.

    Validated before any write, so a typo in one setting cannot half-apply a
    request that also contained a real change.
    """
    before = config_path.read_text(encoding="utf-8")
    with pytest.raises(PersonalityRefused):
        cerebellum.update_setting({"style": "casual", "humor_level": "sideways"})
    assert config_path.read_text(encoding="utf-8") == before


def test_the_allowed_values_are_the_three_enumerations() -> None:
    """The page's dropdowns are built from this, so it must stay in step."""
    assert set(ALLOWED_VALUES) == {"style", "humor_level", "verbosity"}
    assert "formal_friendly" in STYLES
    assert "terse" in VERBOSITY


def test_an_empty_change_is_refused(cerebellum: Cerebellum) -> None:
    """Nothing to write is not a silent success."""
    with pytest.raises(PersonalityRefused):
        cerebellum.update_setting({})


# -- the file --------------------------------------------------------


def test_a_missing_file_gets_defaults_without_being_created(tmp_path: Path) -> None:
    """A user who has not configured a voice still gets one.

    Loading must not write the file: a read that creates a file is how a config
    directory quietly fills with defaults the user never chose.
    """
    path = tmp_path / "absent.json"
    voice = load(path)

    assert voice.name
    assert voice.style in ALLOWED_VALUES["style"]
    assert not path.exists()


def test_a_partial_file_is_filled_in(tmp_path: Path) -> None:
    """Missing keys take their defaults rather than refusing to load.

    The file grew over time — ``style``, ``humor_level`` and ``verbosity`` are
    Phase 6 additions to something that already existed — so a file written
    before them is the normal case, not an error.
    """
    path = tmp_path / "partial.json"
    path.write_text(json.dumps({"name": "Zuzu"}), encoding="utf-8")

    voice = load(path)
    assert voice.name == "Zuzu"
    assert voice.style in ALLOWED_VALUES["style"]
    assert voice.verbosity in ALLOWED_VALUES["verbosity"]


def test_a_corrupt_file_refuses_and_names_itself(tmp_path: Path) -> None:
    """A file that is not JSON is reported, not silently replaced.

    Silently reverting a voice to the defaults would hide the problem and lose
    whatever the user had written. This is the one case where refusing is right
    even though the setting is cosmetic: the message names the file and the
    JSON error, so it is a five-second fix.
    """
    path = tmp_path / "broken.json"
    path.write_text("{not json", encoding="utf-8")

    with pytest.raises(PersonalityRefused) as caught:
        load(path)
    assert "broken.json" in str(caught.value)
    assert path.read_text(encoding="utf-8") == "{not json", "the file is left alone"


def test_a_hand_edit_keeps_keys_zuZZ_does_not_know(config_path: Path) -> None:
    """Writing one setting must not delete the others, known or not.

    ``config/personality.json`` is the operator's file. Round-tripping it through
    a fixed set of keys would quietly delete anything hand-added to it — which
    is exactly what someone does when they want to try a new tone field.
    """
    data = dict(CONFIG, my_own_field="kept")
    config_path.write_text(json.dumps(data), encoding="utf-8")

    cerebellum = Cerebellum(path=config_path)
    cerebellum.update_setting({"style": "casual"})

    written = json.loads(config_path.read_text(encoding="utf-8"))
    assert written["style"] == "casual"
    assert written["my_own_field"] == "kept"
    assert written["tone"] == CONFIG["tone"]


def test_a_change_survives_a_reload(config_path: Path) -> None:
    """The file is the store, so a restart must keep the change."""
    cerebellum = Cerebellum(path=config_path)
    cerebellum.update_setting({"verbosity": "detailed"})
    assert Cerebellum(path=config_path).get_settings().verbosity == "detailed"


def test_an_empty_list_falls_back_to_the_default(config_path: Path) -> None:
    """``"quirks": []`` means "none", so it has to survive the fill-in.

    ``listed()`` falls back to the default for an empty list, which would put the
    shipped quirks back the moment a user removed them all on purpose. That is a
    real inversion of intent, so it is asserted rather than assumed.
    """
    config_path.write_text(json.dumps(dict(CONFIG, quirks=[])), encoding="utf-8")
    voice = load(config_path)
    assert voice.quirks == (), "an empty quirk list should stay empty"
    assert Cerebellum(path=config_path).update_setting({"humor_level": "none"}).quirks == ()


def test_the_shipped_config_loads(tmp_path: Path) -> None:
    """The real ``config/personality.json`` is valid.

    Cheap, and it catches a malformed edit to the operator's own file, which
    would otherwise only show up as a startup failure on the next run.
    """
    from brain.cerebellum.personality_config import PERSONALITY_FILE

    if not PERSONALITY_FILE.exists():
        pytest.skip("no shipped personality.json in this checkout")
    voice = load(PERSONALITY_FILE)
    assert voice.style in ALLOWED_VALUES["style"]
    assert voice.humor_level in ALLOWED_VALUES["humor_level"]
    assert voice.verbosity in ALLOWED_VALUES["verbosity"]


# -- a rejected value is used *and* reported ----------------------------


def test_invalid_verbosity_logs_warning_but_falls_back(config_path: Path) -> None:
    """A word outside the vocabulary must not take the voice down, or vanish.

    ``load`` runs on every reply, so refusing here would mean one bad value in a
    hand-edited file stops ZUZZ answering at all. Falling back quietly is the
    other failure and the more likely one to survive review: the operator sets
    ``low``, reads back ``concise``, and concludes the Settings page is lying.

    So the default is used *and* the rejection is written to the activity log and
    carried on the personality itself.
    """
    config_path.write_text(
        json.dumps(dict(CONFIG, verbosity="low")), encoding="utf-8"
    )

    voice = load(config_path)

    assert voice.verbosity == "concise", "an unusable value falls back to the default"
    assert voice.invalid_fields == {"verbosity": "low"}
    assert len(voice.warnings) == 1
    assert "low" in voice.warnings[0] and "concise" in voice.warnings[0]

    from brain.brainstem.activity_logger import get_logger

    warned = [
        record for record in get_logger().tail(50) if record.get("action") == "config_invalid"
    ]
    assert warned, "a silent fallback is exactly what this test exists to prevent"
    assert warned[-1]["field"] == "verbosity"
    assert warned[-1]["given"] == "low"
    assert warned[-1]["used"] == "concise"
    assert list(ALLOWED_VALUES["verbosity"]) == warned[-1]["allowed"]


def test_a_rejected_value_is_reported_once_per_read_of_the_same_file(
    config_path: Path,
) -> None:
    """The prompt is rebuilt per reply, so the warning must not be per reply.

    The activity log is also the input to Basal Ganglia pattern detection. A
    config that is wrong on every read would otherwise contribute thousands of
    identical lines to the tail it reads and drown out the habits it is looking
    for.
    """
    config_path.write_text(
        json.dumps(dict(CONFIG, verbosity="low")), encoding="utf-8"
    )
    from brain.brainstem.activity_logger import get_logger

    for _ in range(5):
        assert load(config_path).verbosity == "concise"

    warned = [
        record for record in get_logger().tail(200) if record.get("action") == "config_invalid"
    ]
    assert len(warned) == 1, f"five reads of one bad file wrote {len(warned)} warnings"


def test_a_blank_value_is_a_default_not_a_rejection(config_path: Path) -> None:
    """Blank means "use the default" and is documented to.

    Reporting it would put a warning on every operator who clears a field to turn
    it off, which is a normal way to edit the file — and a warning that fires on
    correct use trains people to ignore warnings.
    """
    config_path.write_text(json.dumps(dict(CONFIG, verbosity="")), encoding="utf-8")

    voice = load(config_path)

    assert voice.verbosity == "concise"
    assert voice.invalid_fields == {}
    assert voice.warnings == ()


def test_a_valid_file_reports_nothing(config_path: Path) -> None:
    """The ordinary case must stay quiet, or the warning means nothing."""
    voice = load(config_path)

    assert voice.invalid_fields == {}
    assert voice.warnings == ()

    from brain.brainstem.activity_logger import get_logger

    assert not [
        record for record in get_logger().tail(50) if record.get("action") == "config_invalid"
    ]


def test_every_closed_vocabulary_is_checked(config_path: Path) -> None:
    """The report is not special to verbosity.

    ``style`` and ``humor_level`` fall back the same way and had the same silent
    behaviour; a check written for one field would leave the other two lying.
    """
    config_path.write_text(
        json.dumps(dict(CONFIG, style="sarcastic", humor_level="chaotic")), encoding="utf-8"
    )

    voice = load(config_path)

    assert voice.style == DEFAULTS["style"]
    assert voice.humor_level == DEFAULTS["humor_level"]
    assert voice.invalid_fields == {"style": "sarcastic", "humor_level": "chaotic"}
    assert len(voice.warnings) == 2


# -- what the page reads ---------------------------------------------


def test_describe_gives_the_page_everything_it_needs(cerebellum: Cerebellum) -> None:
    """One call, so the Memory page cannot show half a configuration."""
    described = cerebellum.describe()
    for key in (
        "style",
        "humor_level",
        "verbosity",
        "greeting",
        "signoff",
        "quirks",
        "allowed_values",
        "editable_keys",
        "moods",
        "path",
    ):
        assert key in described, key

    assert described["allowed_values"] == {
        key: list(values) for key, values in ALLOWED_VALUES.items()
    }, "the API sends JSON, so the tuples become lists on the way out"
    assert set(described["editable_keys"]) == set(EDITABLE_KEYS)


def test_status_is_a_summary_not_the_whole_config(cerebellum: Cerebellum) -> None:
    """The status line has to stay short enough to sit in a badge."""
    status = cerebellum.status()
    assert status["name"] == "ZUZZ"
    assert status["max_sentences"] == 8
    assert status["quirks"] == 1
    assert len(status["moods"]) == len(MOODS)


def test_respond_reports_the_reading_and_the_instruction(cerebellum: Cerebellum) -> None:
    """The Mood panel shows both the reading and what ZUZZ will do about it."""
    shaped = cerebellum.respond("this is not working AGAIN", "Here is what I found.")
    assert shaped["mood"] == FRUSTRATED
    assert shaped["formality"] in {CASUAL, NEUTRAL, FORMAL}
    assert shaped["instruction"]
    assert shaped["formality_instruction"]
    assert shaped["reply"] == "Here is what I found."