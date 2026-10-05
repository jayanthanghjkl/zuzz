"""Cerebellum — personality configuration.

Reads and writes ``config/personality.json``, and turns it into a typed
:class:`Personality` so no caller has to know what the file looks like.

**Everything has a default.** The file is user-editable and a user who deletes a
key — or a fresh checkout that has not been configured yet — must get a working
voice rather than a ``KeyError`` in the middle of composing a reply. Every
accessor below is therefore total.

**A total accessor is not an excuse to be quiet.** A value outside a closed
vocabulary — ``"verbosity": "low"``, say, where the field wants one of ``terse``,
``concise`` or ``detailed`` — cannot reach a model as an instruction to interpret,
so the default is used. Doing that *silently* is its own defect: the operator
reads the page back, sees a value they did not write, and concludes the page is
lying. Every rejection is therefore reported twice — once on the activity log,
once as ``invalid_fields`` and ``warnings`` on the personality itself, which is
what ``GET /api/personality/settings`` returns.

**Only some keys are writable at runtime,** and the split is deliberate. The
voice can change: style, humour, verbosity, greeting, signoff and quirks are the
user tuning how ZUZZ sounds, and a tool call to do that is harmless. The identity
cannot: ``name``, ``user_name``, ``owner``, ``wake_word`` and
``max_sentences_per_reply`` are structural, and a model able to rewrite its own
name or its own output budget can make every prompt assertion downstream a lie.
:data:`EDITABLE_KEYS` is the whole authority of
:meth:`~brain.cerebellum.personality_config.PersonalitySettings.update`, and it is
a closed set rather than a denylist so a key added to the file later is
unwritable by default.

Reads go through :func:`core.config.get_personality` like every other region, so
there is one place that knows the on-disk layout.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Set, Tuple

from core.config import CONFIG_DIR, get_personality

__all__ = [
    "ALLOWED_VALUES",
    "DEFAULTS",
    "EDITABLE_KEYS",
    "HUMOR_LEVELS",
    "Personality",
    "PersonalityRefused",
    "PersonalitySettings",
    "SIGNING_OFF",
    "STYLES",
    "VERBOSITY",
    "load",
    "read_raw",
    "write_changes",
]

#: The config file this module owns.
PERSONALITY_FILE = CONFIG_DIR / "personality.json"

#: How ZUZZ is introduced. Configurable because a user who renames the machine's
#: assistant should not have to edit prompt code to make it stick.
SIGNING_OFF = "ZUZZ"

#: Every field, with the value used when the file omits it or states it blank.
#: The defaults describe the voice the rest of the system already assumes — the
#: Cerebrum's prompt says "direct, accurate and honest" and writes prose under
#: eight sentences — so a missing file produces the behaviour that was already
#: there rather than a new one.
DEFAULTS: Dict[str, Any] = {
    "name": SIGNING_OFF,
    "user_name": "",
    "owner": "",
    "wake_word": "",
    "tone": ["direct", "warm", "concise"],
    "style": "formal_friendly",
    "humor_level": "subtle",
    "verbosity": "concise",
    "max_sentences_per_reply": 8,
    "greeting": "",
    "signoff": "",
    "quirks": [],
}

#: The keys :meth:`PersonalitySettings.update` will accept. A closed set, not a
#: denylist — a key added to the file next year is unwritable until somebody says
#: otherwise, which is the direction that fails safely.
EDITABLE_KEYS: Tuple[str, ...] = (
    "style",
    "humor_level",
    "verbosity",
    "greeting",
    "signoff",
    "quirks",
)

#: Closed vocabularies. A style outside the list is refused rather than passed
#: through to the prompt, because an unrecognised value would be handed to a
#: model as an instruction it has to interpret — and an interpreted instruction
#: is a weaker guarantee than an enumerated one.
STYLES: Tuple[str, ...] = ("formal_friendly", "casual", "terse", "playful", "neutral")

HUMOR_LEVELS: Tuple[str, ...] = ("none", "subtle", "playful")

VERBOSITY: Tuple[str, ...] = ("terse", "concise", "detailed")

#: Which closed vocabulary each key draws from. Keys absent from this mapping
#: are free text.
ALLOWED_VALUES: Dict[str, Tuple[str, ...]] = {
    "style": STYLES,
    "humor_level": HUMOR_LEVELS,
    "verbosity": VERBOSITY,
}

#: How long a single free-text personality field may be. A greeting is a sentence
#: and a quirk is a clause; anything longer is a prompt injection attempt aimed
#: at the Cerebrum rather than a personality setting.
MAX_FIELD_CHARS = 300


class PersonalityRefused(ValueError):
    """A personality setting is not one ZUZZ will accept.

    Raised before anything is written, so a refused update leaves the file
    exactly as it was.
    """


#: The name placeholders a greeting or a sign-off may use. Both spellings mean
#: "put a name here" and both have to work: a config that says ``{name}`` while
#: the code only knows ``{user_name}`` does not fail, it renders — and the result
#: is the user's own name appended after an untouched template, which is how
#: ``"Hey {user_name} Jayanthan"`` reached a reply once already.
_PLACEHOLDER_RE = re.compile(r"\{(user_name|name)\}", re.IGNORECASE)

#: Punctuation that is only there because a name was going to sit next to it. A
#: greeting with nothing in it reads better as "Hey!" than as "Hey !", and
#: better than as "Hey , welcome!".
_HANGING = " \t,:;-–—"

#: Matches the space a removed placeholder leaves in front of punctuation.
_SPACE_BEFORE_PUNCT_RE = re.compile(r"\s+([.,:;!?])")

#: Matches a run of whitespace left behind by a removal.
_SPACE_RUN_RE = re.compile(r"\s+")


def _render(template: str, names: Dict[str, str]) -> str:
    """Fill a name template in, and tidy up whatever is left.

    The tidy-up is the part that matters. Removing a placeholder from
    ``"Hey {user_name}!"`` leaves ``"Hey !"``, and shipping that is worse than
    shipping nothing: a line of punctuation with a gap in it reads as a bug in
    the program rather than as a greeting to nobody.

    Args:
        template: The configured line, which may contain name placeholders.
        names: What each placeholder stands for. A placeholder with no entry, or
            an entry that is blank, is removed rather than left visible.

    Returns:
        The rendered line, or an empty string when nothing survived.
    """
    text = str(template or "").strip()
    if not text:
        return ""
    removed = False

    def replace(match: "re.Match[str]") -> str:
        nonlocal removed
        value = str(names.get(match.group(1).lower(), "") or "").strip()
        if value:
            return value
        removed = True
        return ""

    text = _PLACEHOLDER_RE.sub(replace, text)
    if removed:
        text = _SPACE_RUN_RE.sub(" ", text)
        text = _SPACE_BEFORE_PUNCT_RE.sub(r"\1", text)
        text = text.strip(_HANGING).strip()
    return text.strip()


@dataclass(frozen=True)
class Personality:
    """ZUZZ's voice, as configured.

    Attributes:
        name: What ZUZZ is called.
        user_name: What to call the user. Empty until configured, which is a
            supported state — a greeting simply does not use a name.
        owner: The account label used by the cloud connectors. Distinct from
            ``user_name`` on purpose: see ``config/personality.json``.
        wake_word: What the user says to get attention.
        tone: Adjectives describing the voice, for the system prompt.
        style: How formal the voice is.
        humor_level: How much humour to allow.
        verbosity: How much to say.
        max_sentences: The prose budget per reply.
        greeting: An opening line. Empty means "do not open with a greeting",
            which is the right default for a reply rather than a first contact.
        signoff: A closing line. Empty means never sign off.
        quirks: Small habits, rendered into the prompt as instructions.
        invalid_fields: Values the file stated that are not in that key's closed
            vocabulary, mapped to what was actually written. Empty when the file is
            valid; non-empty means the value in use is the default rather than the
            one the operator asked for.
    """

    name: str = DEFAULTS["name"]
    user_name: str = DEFAULTS["user_name"]
    owner: str = DEFAULTS["owner"]
    wake_word: str = DEFAULTS["wake_word"]
    tone: Tuple[str, ...] = tuple(DEFAULTS["tone"])
    style: str = DEFAULTS["style"]
    humor_level: str = DEFAULTS["humor_level"]
    verbosity: str = DEFAULTS["verbosity"]
    max_sentences: int = int(DEFAULTS["max_sentences_per_reply"])
    greeting: str = DEFAULTS["greeting"]
    signoff: str = DEFAULTS["signoff"]
    quirks: Tuple[str, ...] = tuple(DEFAULTS["quirks"])
    raw: Dict[str, Any] = field(default_factory=dict, repr=False, compare=False)
    invalid_fields: Dict[str, str] = field(
        default_factory=dict, repr=False, compare=False
    )

    @property
    def warnings(self) -> Tuple[str, ...]:
        """Return one sentence per rejected field, for the log and for the API.

        The same sentences the activity log records, so an operator watching the
        log and a user reading ``GET /api/personality/settings`` are told the
        identical thing rather than two versions of it.
        """
        return tuple(
            _invalid_message(key, value)
            for key, value in sorted(self.invalid_fields.items())
        )

    def to_dict(self) -> Dict[str, Any]:
        """Return the whole configuration as plain data, for the API."""
        return {
            "name": self.name,
            "user_name": self.user_name,
            "owner": self.owner,
            "wake_word": self.wake_word,
            "tone": list(self.tone),
            "style": self.style,
            "humor_level": self.humor_level,
            "verbosity": self.verbosity,
            "max_sentences_per_reply": self.max_sentences,
            "greeting": self.greeting,
            "signoff": self.signoff,
            "quirks": list(self.quirks),
            "invalid_fields": dict(self.invalid_fields),
            "warnings": list(self.warnings),
            "editable_keys": list(EDITABLE_KEYS),
            "allowed_values": {key: list(values) for key, values in ALLOWED_VALUES.items()},
        }

    @property
    def address_name(self) -> str:
        """The name to use when addressing the user.

        ``user_name`` is the field the Cerebellum documents for this, and
        ``owner`` is the fallback. They are normally the same person, but they
        are separate keys in the file, and a config that set only one of them
        should still produce a greeting with a name in it rather than a
        nameless one.
        """
        return self.user_name.strip() or self.owner.strip()

    def addressed_greeting(self, template: Optional[str] = None) -> str:
        """Return the greeting with the user's name filled in.

        The name is substituted here rather than written into the config, so a
        user who renames themselves changes one field instead of editing every
        string that greets them.

        Args:
            template: Render this line instead of the configured greeting. Used
                by the tests that pin the substitution rules; production callers
                pass nothing.

        Returns:
            The rendered greeting. Empty when the template is empty, and also
            when it is nothing but a name and no name is configured — a
            greeting that greets nobody reads worse than none.
        """
        source = self.greeting if template is None else str(template or "")
        source = str(source or "").strip()
        if not source:
            return ""

        name = self.address_name
        if _PLACEHOLDER_RE.search(source):
            # Both placeholders mean the person being addressed, so a template
            # written either way renders to the same line.
            return _render(source, {"user_name": name, "name": name})
        if not name or name in source:
            return _render(source, {})
        return f"{_render(source, {})} {name}".strip()

    def signed_off(self, template: Optional[str] = None) -> str:
        """Return the sign-off, with its placeholders filled in, or nothing.

        ``{name}`` here is ZUZZ signing its own name, which is what a sign-off
        almost always means, and ``{user_name}`` is there for the template that
        addresses the person instead. The substitution and the clean removal of
        a blank one are the same as the greeting's, because a half-rendered
        closing line is the same defect wherever it appears.

        Args:
            template: Render this line instead of the configured sign-off.

        Returns:
            The rendered sign-off, or an empty string when there is none.
        """
        source = self.signoff if template is None else str(template or "")
        return _render(source, {"name": self.name, "user_name": self.address_name})


def read_raw(path: Optional[Path] = None) -> Dict[str, Any]:
    """Return the config file as a plain dict, copied.

    A copy, because :func:`core.config.get_personality` is memoised and handing
    out its dict would let a caller mutate the cache for the whole process.

    Args:
        path: The file to read. Defaults to ``config/personality.json``.

    Returns:
        The parsed configuration, or an empty dict when the file is missing.
    """
    if path is not None and Path(path) != PERSONALITY_FILE:
        # A caller reading somewhere other than the real config — a test with a
        # temporary file, a future per-profile setting. Read directly rather
        # than through the memoised accessor, which only knows the real path.
        target = Path(path)
        if not target.is_file():
            return {}
        try:
            return json.loads(target.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise PersonalityRefused(f"invalid JSON in {target}: {exc}") from exc
    return dict(get_personality())


def _read(path: Optional[Path]) -> Dict[str, Any]:
    """Read the configuration, preferring the shared accessor for the real file."""
    return read_raw(path)


def _invalid_message(key: str, value: str) -> str:
    """Return the sentence that reports one rejected value.

    One place builds it, because the sentence is the contract: it goes in the
    activity log and comes back from the API, and a user comparing the two should
    not be left wondering whether they describe the same problem.

    Args:
        key: The setting that was rejected.
        value: What the file actually said.

    Returns:
        A sentence naming the field, the value, the legal values and what is being
        used instead.
    """
    return (
        f"personality.{key}={value!r} is not one of "
        f"{', '.join(ALLOWED_VALUES[key])}; falling back to {DEFAULTS[key]!r}"
    )


#: Problems already reported this process. :func:`load` runs on every prompt build,
#: and the activity log is also what the Basal Ganglia reads for habits, so a
#: warning written on every read would fill the file with one identical line and
#: bury the patterns the log exists to find.
_REPORTED: Set[Tuple[str, str, str]] = set()


def _report_invalid(path: Optional[Path], problems: Mapping[str, str]) -> None:
    """Warn about every value that was rejected, once per distinct problem.

    A log line rather than an exception. A config the operator hand-edited into an
    invalid state must still produce a working voice — ``load`` is on the path of
    every reply — but it must not do so quietly: a user who cannot see that their
    setting was dropped has no way to fix it and concludes the page is lying.

    Args:
        path: The file the values came from, part of the de-duplication key so two
            profiles with the same mistake are both reported.
        problems: Each rejected field and the value it held.
    """
    if not problems:
        return
    # Imported here rather than at module scope so that importing the
    # Cerebellum does not open the log file, and so the isolated logger the tests
    # install is the one that gets used.
    from brain.brainstem.activity_logger import get_logger

    logger = get_logger()
    where = str(PERSONALITY_FILE if path is None else path)
    for key, value in sorted(problems.items()):
        signature = (where, key, value)
        if signature in _REPORTED:
            continue
        _REPORTED.add(signature)
        logger.log(
            "cerebellum",
            "config_invalid",
            field=key,
            given=value,
            used=str(DEFAULTS[key]),
            allowed=list(ALLOWED_VALUES[key]),
            message=_invalid_message(key, value),
        )


def load(path: Optional[Path] = None) -> Personality:
    """Return the configured personality, with defaults filled in.

    A value outside a closed vocabulary falls back to the default **and is
    reported**: on the activity log, and as ``invalid_fields`` and ``warnings`` on
    the personality the caller receives. Blank is not reported, because blank
    means "use the default" and is documented to do so.

    Args:
        path: The file to read. Defaults to ``config/personality.json``.

    Returns:
        A complete :class:`Personality`. Never raises for a missing or partial
        file — a user who has not configured a voice still gets one.
    """
    data = _read(path)
    problems: Dict[str, str] = {}

    def text(key: str) -> str:
        value = str(data.get(key, "") or "").strip()
        return value or str(DEFAULTS[key])

    def listed(key: str) -> Tuple[str, ...]:
        value = data.get(key, DEFAULTS[key])
        if isinstance(value, str):
            # A single string where a list was expected is a hand-edit slip, and
            # reading it as a one-item list is more useful than refusing to load.
            items = [value.strip()] if value.strip() else []
        elif isinstance(value, (list, tuple)):
            items = [str(item).strip() for item in value if str(item).strip()]
        else:
            items = []
        return tuple(items) if items else tuple(DEFAULTS[key])

    def enumerated(key: str) -> str:
        raw = str(data.get(key, "") or "").strip()
        value = raw.lower()
        if value in ALLOWED_VALUES[key]:
            return value
        # The value cannot reach a model as an instruction to interpret, so the
        # default is the safe answer — but recording what was rejected is what
        # turns "your config was ignored" into something the operator can act on.
        if raw:
            problems[key] = raw
        return str(DEFAULTS[key])

    try:
        sentences = int(data.get("max_sentences_per_reply", DEFAULTS["max_sentences_per_reply"]))
    except (TypeError, ValueError):
        sentences = int(DEFAULTS["max_sentences_per_reply"])
    sentences = max(1, min(50, sentences))

    # Built first, then reported: ``enumerated`` is what fills ``problems``, so
    # reporting above the constructor would log an empty problem set every time.
    voice = Personality(
        name=text("name"),
        user_name=str(data.get("user_name", "") or "").strip(),
        owner=str(data.get("owner", "") or "").strip(),
        wake_word=text("wake_word") if str(data.get("wake_word", "") or "").strip() else "",
        tone=listed("tone"),
        style=enumerated("style"),
        humor_level=enumerated("humor_level"),
        verbosity=enumerated("verbosity"),
        max_sentences=sentences,
        greeting=str(data.get("greeting", "") or "").strip(),
        signoff=str(data.get("signoff", "") or "").strip(),
        quirks=listed("quirks"),
        raw=data,
        invalid_fields=problems,
    )
    _report_invalid(path, problems)
    return voice


def _validate(key: str, value: Any) -> Any:
    """Return one validated setting value.

    Args:
        key: The setting name.
        value: The value offered.

    Returns:
        The value, coerced to the type the key holds.

    Raises:
        PersonalityRefused: When the key is not editable, the value is outside a
            closed vocabulary, a free-text field is too long, or a list field was
            given something that is not a list.
    """
    if key not in EDITABLE_KEYS:
        raise PersonalityRefused(
            f"{key!r} is not editable at runtime; ZUZZ can change its "
            f"appearance but not its identity. Editable: {', '.join(EDITABLE_KEYS)}"
        )
    allowed = ALLOWED_VALUES.get(key)
    if allowed is not None:
        text = str(value).strip().lower()
        if text not in allowed:
            raise PersonalityRefused(
                f"{key} must be one of: {', '.join(allowed)} — got {value!r}"
            )
        return text
    if key == "quirks":
        if not isinstance(value, (list, tuple)):
            raise PersonalityRefused(
                "quirks must be a list of short phrases, not a single string"
            )
        cleaned: List[str] = []
        for item in value:
            text = str(item).strip()
            if not text:
                continue
            if len(text) > MAX_FIELD_CHARS:
                raise PersonalityRefused(
                    f"a quirk may be at most {MAX_FIELD_CHARS} characters"
                )
            cleaned.append(text)
        return cleaned
    text = str(value).strip()
    if len(text) > MAX_FIELD_CHARS:
        raise PersonalityRefused(f"{key} may be at most {MAX_FIELD_CHARS} characters")
    return text


def write_changes(changes: Dict[str, Any], path: Optional[Path] = None) -> Personality:
    """Apply personality changes to the config file.

    Validates **every** key before writing any of them, so a request naming one
    good setting and one bad one changes nothing rather than half-applying. The
    file is written via a temp file and renamed, because a personality edit that
    truncates the file takes the voice down for the whole machine.

    Args:
        changes: The settings to change.
        path: The file to write. Defaults to ``config/personality.json``.

    Returns:
        The personality as it now stands.

    Raises:
        PersonalityRefused: When any key is not editable or any value is invalid.
        OSError: When the file could not be written.
    """
    if not isinstance(changes, dict) or not changes:
        raise PersonalityRefused("a personality update needs at least one setting")
    cleaned = {key: _validate(str(key), value) for key, value in changes.items()}

    target = Path(path) if path is not None else PERSONALITY_FILE
    merged = _read(path)
    for key, value in cleaned.items():
        merged[key] = value
    _write(merged, target)

    # The accessor is memoised process-wide, so the cache has to go or every
    # later read returns the voice the user just changed away from. Cleared
    # unconditionally rather than only for the real file: clearing a cache that
    # was not populated costs nothing, and not clearing it when it was would
    # leave the process disagreeing with the disk.
    get_personality.cache_clear()
    return load(path)


def _write(data: Dict[str, Any], target: Path) -> None:
    """Write the configuration, replacing the file atomically.

    Args:
        data: The full configuration to persist.
        target: The file to write.

    Raises:
        OSError: When the file could not be written.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix(target.suffix + ".tmp")
    temp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temp.replace(target)


class PersonalitySettings:
    """The settings surface the API and the tools talk to.

    Thin on purpose. Every method here is a one-liner over :func:`load` and
    :func:`write_changes`, and the reason it exists as a class at all is so the
    Face layer can hold one object on ``app.state`` alongside the other regions
    rather than reaching for module functions.

    Attributes:
        path: Where the configuration lives, reported for the API.
    """

    def __init__(self, path: Path = PERSONALITY_FILE) -> None:
        """Bind the settings to a configuration file.

        Args:
            path: The file to read and write.
        """
        self.path = Path(path)

    def current(self) -> Personality:
        """Return the configured personality."""
        return load(self.path)

    def update(self, changes: Dict[str, Any]) -> Personality:
        """Apply changes to the configuration.

        Args:
            changes: The settings to change.

        Returns:
            The personality as it now stands.

        Raises:
            PersonalityRefused: When any key or value is not acceptable.
        """
        return write_changes(changes, self.path)

    def describe(self) -> Dict[str, Any]:
        """Return the configuration plus what may be changed and to what."""
        payload = self.current().to_dict()
        payload["path"] = str(self.path)
        payload["max_field_chars"] = MAX_FIELD_CHARS
        return payload